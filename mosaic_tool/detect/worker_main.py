"""検出ワーカー: venv 側の Python で動き、標準入出力で JSON をやり取りする

このファイルは runtime\\ へコピーされ venv の Python から実行されるため、
本体パッケージを import してはならない(venv には入っていない)。
ultralytics も関数の中で import し、本体側のテストから読み込めるようにする。

使い方: python detect_worker.py <model1.pt> <model2.pt> ...
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# プロトコル(1 行 = 1 JSON 応答)に使う標準出力。reserve_protocol_stdout() で確保する
_protocol_out = sys.stdout

# torch のビルドに載っていない GPU で推論したときの CUDA エラー
_NO_KERNEL_IMAGE = "no kernel image is available"
_NO_KERNEL_IMAGE_HINT = (
    "この GPU に対応していない torch が入っています。"
    "自動検出ウィンドウの「再セットアップ」からやり直してください。"
)


def error_message(e: Exception) -> str:
    """例外を利用者向けのメッセージへ整える(原因が分かるものは対処も添える)"""
    detail = f"{type(e).__name__}: {e}"
    if _NO_KERNEL_IMAGE in str(e):
        return f"{_NO_KERNEL_IMAGE_HINT}\n\n{detail}"
    return detail


def reserve_protocol_stdout() -> None:
    """応答用に本物の標準出力を確保し、それ以外の出力を標準エラーへ逃がす

    ultralytics は初回 import 時に設定ファイルの作成メッセージを標準出力へ書く。
    1 行 = 1 応答の前提が崩れると呼び出し側が JSON として解釈できず、
    「初回だけ検出に失敗する」状態になるため、ライブラリを import する前に呼ぶ。
    """
    global _protocol_out
    _protocol_out = sys.stdout
    sys.stdout = sys.stderr


def load_models(model_paths: list[str]) -> list[tuple]:
    """モデルを読み込む((表示名, モデル) の列を返す)"""
    from ultralytics import YOLO

    return [(Path(p).name, YOLO(p)) for p in model_paths]


def class_ids(model, names: list) -> list | None:
    """クラス名を model.names の ID へ変換する

    指定が空、または 1 つも一致しない場合は None(= 全クラス)を返す。
    モデルを差し替えてクラス名が総入れ替えになったとき、
    何も検出されない状態になるのを避けるため。
    """
    if not names:
        return None
    wanted = {str(n) for n in names}
    table = getattr(model, "names", {}) or {}
    ids = [i for i in sorted(table) if str(table[i]) in wanted]
    return ids or None


def model_classes(models: list[tuple]) -> dict:
    """読み込み済みモデルのクラス名一覧({ファイル名: 名前の列})"""
    result = {}
    for name, model in models:
        table = getattr(model, "names", {}) or {}
        result[name] = [str(table[i]) for i in sorted(table)]
    return result


def detect(
    models: list[tuple],
    image_path: str,
    specs: dict,
    device: str,
    on_progress=None,
) -> list[dict]:
    """指定されたモデルだけで推論し、検出を 1 つの列にまとめて返す

    specs はファイル名をキー、{"conf": 信頼度(0〜1), "classes": クラス名} を値とする。
    ここに無いモデルは読み込み済みでも推論しない。
    """
    targets = [(name, model) for name, model in models if name in specs]
    detections: list[dict] = []
    for done, (name, model) in enumerate(targets, start=1):
        spec = specs[name]
        # device が空なら ultralytics の自動選択に任せる
        result = model(
            image_path,
            conf=float(spec.get("conf", 0.25)),
            device=device or None,
            verbose=False,
            classes=class_ids(model, spec.get("classes") or []),
        )[0]
        polygons = result.masks.xy if result.masks is not None else None
        for i, box in enumerate(result.boxes):
            item = {
                "model": name,
                "conf": float(box.conf[0]),
                "bbox": [float(v) for v in box.xyxy[0].tolist()],
            }
            # セグメンテーション対応モデルなら画像座標の輪郭がそのまま得られる
            if polygons is not None and i < len(polygons):
                item["polygon"] = [[float(x), float(y)] for x, y in polygons[i]]
            detections.append(item)
        if on_progress is not None:
            on_progress(done, len(targets), name)
    return detections


def handle_request(models: list[tuple], line: str, emit) -> dict:
    """リクエスト 1 行を処理して最終応答を返す(失敗しても例外を外へ出さない)

    emit はモデル 1 件の推論が終わるたびに進捗の応答で呼ばれる。
    """
    try:
        request = json.loads(line)
        if request.get("command") == "classes":
            return {"ok": True, "classes": model_classes(models)}
        specs = {
            str(name): dict(spec)
            for name, spec in (request.get("models") or {}).items()
        }
        detections = detect(
            models,
            request["image"],
            specs,
            request.get("device", ""),
            on_progress=lambda done, total, name: emit(
                {"ok": True, "progress": {"done": done, "total": total, "model": name}}
            ),
        )
        return {"ok": True, "detections": detections}
    except Exception as e:
        return {"ok": False, "error": error_message(e)}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), file=_protocol_out, flush=True)


def main(argv: list[str]) -> int:
    # ultralytics を読み込む前に、応答用の標準出力を確保する
    reserve_protocol_stdout()
    model_paths = argv[1:]
    if not model_paths:
        _emit({"ok": False, "error": "検出モデルが指定されていません"})
        return 1
    try:
        models = load_models(model_paths)
    except Exception as e:
        _emit({"ok": False, "error": f"モデルの読み込みに失敗しました: {e}"})
        return 1
    # 読み込み完了を伝える(呼び出し側はこれを待ってからリクエストを送る)
    _emit({"ok": True, "ready": True})
    for line in sys.stdin:
        if line.strip():
            _emit(handle_request(models, line, _emit))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
