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

def load_models(model_paths: list[str]) -> list[tuple]:
    """モデルを読み込む((表示名, モデル) の列を返す)"""
    from ultralytics import YOLO

    return [(Path(p).name, YOLO(p)) for p in model_paths]


def detect(
    models: list[tuple],
    image_path: str,
    confidences: dict,
    device: str,
    on_progress=None,
) -> list[dict]:
    """指定されたモデルだけで推論し、検出を 1 つの列にまとめて返す

    confidences はファイル名をキー、信頼度(0〜1)を値とする。
    ここに無いモデルは読み込み済みでも推論しない。
    """
    targets = [(name, model) for name, model in models if name in confidences]
    detections: list[dict] = []
    for done, (name, model) in enumerate(targets, start=1):
        # device が空なら ultralytics の自動選択に任せる
        result = model(
            image_path,
            conf=confidences[name],
            device=device or None,
            verbose=False,
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
        confidences = {
            str(name): float(conf)
            for name, conf in (request.get("models") or {}).items()
        }
        detections = detect(
            models,
            request["image"],
            confidences,
            request.get("device", ""),
            on_progress=lambda done, total, name: emit(
                {"ok": True, "progress": {"done": done, "total": total, "model": name}}
            ),
        )
        return {"ok": True, "detections": detections}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv: list[str]) -> int:
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
