# 自動検出ウィンドウ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自動検出の操作を独立したモードレスウィンドウへ移し、モデル単位の ON/OFF と信頼度設定、標準モデルの自動ダウンロード、検出の進捗表示を可能にする。

**Architecture:** 新規 UI は `mosaic_tool/detect/` 配下に 3 ファイル(`catalog.py` / `downloader.py` / `detect_window.py`)として追加する。本体とワーカーの JSON プロトコルにモデル別の信頼度と進捗を追加し、モデルの有効/無効は `AppSettings` にファイル名キーで永続化する。ワーカーは `models\` を全件ロードしたまま、リクエストで指定されたモデルだけを推論する。

**Tech Stack:** Python 3.10+, PySide6 (QtWidgets / QtNetwork), pytest

設計: `docs/superpowers/specs/2026-07-28-auto-detect-window-design.md`
モデルの検証記録: `docs/detection-models.md`

## Global Constraints

- コードのコメントとエラーログメッセージは日本語で書く
- `mosaic_tool/detect/worker_main.py` は `mosaic_tool` を import してはならない(venv 側で動くため)。`tests/test_detect_worker_main.py::test_worker_does_not_import_mosaic_tool` がこれを検証している
- 本体は PySide6 + Pillow のみに依存する。ultralytics / torch を本体側で import しない
- テストは `QT_QPA_PLATFORM=offscreen` で動く。GUI テストは既存ファイルの `qapp` フィクスチャの書き方に合わせる
- 標準モデルは HuggingFace `Anzhc/Anzhcs_YOLOs` の 3 件のみ。ファイル名・用途名・サイズ・推奨 conf は下表の値をそのまま使う

| ファイル名 | 用途名 | サイズ | 推奨 conf |
|---|---|---|---|
| `Anzhc Face seg 640 v4 y11n.pt` | 顔 | 5.7MB | 25 |
| `Anzhc Eyes -seg-hd.pt` | 目 | 6.6MB | 40 |
| `Anzhc HeadHair seg y8n.pt` | 髪 | 6.5MB | 25 |

- テストの実行は `python -m pytest` (リポジトリルートから)
- コミットメッセージは Conventional Commits 形式の日本語

## File Structure

| ファイル | 責務 | Task |
|---|---|---|
| `mosaic_tool/detect/catalog.py` (新規) | 標準モデルの定義。GUI 非依存の定数と検索関数のみ | 1 |
| `mosaic_tool/settings.py` (変更) | モデル別の有効/無効と信頼度の永続化 | 2 |
| `mosaic_tool/detect/convert.py` (変更) | リクエスト組み立てと応答の種別判別 | 3 |
| `mosaic_tool/detect/worker_main.py` (変更) | モデル別 conf での推論と進捗出力 | 4 |
| `mosaic_tool/detect/worker_client.py` (変更) | `progress` シグナルとモデル辞書つきリクエスト | 5 |
| `mosaic_tool/canvas.py` (変更) | 一括追加分を非選択にする | 6 |
| `mosaic_tool/detect/downloader.py` (新規) | 1 ファイルのダウンロードと未取得モデルの列挙 | 7 |
| `mosaic_tool/detect/setup_dialog.py` (変更) | 既定 CPU 化と標準モデルの自動取得 | 8 |
| `mosaic_tool/detect/detect_window.py` (新規) | 自動検出ウィンドウ本体 | 9 |
| `mosaic_tool/app.py` (変更) | ツールバーからウィンドウを開く配線 | 10 |
| `README.md` (変更) | 自動検出の手順とその他のモデルの入手先 | 11 |

Task 1〜6 は既存の GUI に触れず、単体テストで閉じる。Task 7〜10 で UI を組み立てる。

---

### Task 1: 標準モデルカタログ

**Files:**
- Create: `mosaic_tool/detect/catalog.py`
- Test: `tests/test_detect_catalog.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `CatalogModel` — `frozen=True` の dataclass。フィールドは `filename: str`, `label: str`, `size_mb: float`, `confidence: int`, `url: str`
  - `MODELS: tuple[CatalogModel, ...]` — 標準モデル 3 件
  - `find(filename: str) -> CatalogModel | None` — ファイル名でカタログを引く
  - `REPO_URL: str` — カタログの出典 URL(README とウィンドウの表示に使う)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_catalog.py`:

```python
"""標準モデルカタログの定義の検証"""
from urllib.parse import unquote

from mosaic_tool.detect import catalog


def test_filenames_are_unique():
    names = [m.filename for m in catalog.MODELS]
    assert len(names) == len(set(names))


def test_all_filenames_end_with_pt():
    assert all(m.filename.endswith(".pt") for m in catalog.MODELS)


def test_urls_point_at_the_repository_with_encoded_filename():
    for model in catalog.MODELS:
        assert model.url.startswith(
            "https://huggingface.co/Anzhc/Anzhcs_YOLOs/resolve/main/"
        )
        # URL エンコードを戻すとファイル名に一致する(空白を含む名前があるため)
        assert unquote(model.url.rsplit("/", 1)[1]) == model.filename


def test_urls_are_percent_encoded():
    # 空白を含む名前がそのまま URL に入っていないこと
    assert all(" " not in model.url for model in catalog.MODELS)


def test_confidence_is_within_percentage_range():
    assert all(1 <= m.confidence <= 100 for m in catalog.MODELS)


def test_every_model_has_a_label_and_size():
    assert all(m.label for m in catalog.MODELS)
    assert all(m.size_mb > 0 for m in catalog.MODELS)


def test_find_returns_the_matching_model():
    assert catalog.find("Anzhc Eyes -seg-hd.pt").label == "目"


def test_find_returns_none_for_unknown_filename():
    assert catalog.find("unknown.pt") is None
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mosaic_tool.detect.catalog'`

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/catalog.py`:

```python
"""標準モデルのカタログ(セットアップ時に自動取得する .pt の定義)

いずれも HuggingFace の Anzhc/Anzhcs_YOLOs から認証なしで取得できる
セグメンテーションモデル。選定と推奨信頼度の根拠は docs/detection-models.md を参照。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

REPO_URL = "https://huggingface.co/Anzhc/Anzhcs_YOLOs"
# HuggingFace の直リンク。CDN へリダイレクトするため取得側は追従が要る
_DOWNLOAD_BASE = f"{REPO_URL}/resolve/main/"
LICENSE = "AGPL-3.0"


@dataclass(frozen=True)
class CatalogModel:
    """標準モデル 1 件の定義"""

    filename: str
    label: str        # 一覧に出す用途名(顔・目・髪)
    size_mb: float
    confidence: int   # 推奨する信頼度しきい値 (%)

    @property
    def url(self) -> str:
        """ダウンロード元(空白を含む名前があるため URL エンコードする)"""
        return _DOWNLOAD_BASE + quote(self.filename)


MODELS: tuple[CatalogModel, ...] = (
    CatalogModel("Anzhc Face seg 640 v4 y11n.pt", "顔", 5.7, 25),
    CatalogModel("Anzhc Eyes -seg-hd.pt", "目", 6.6, 40),
    # 髪の推奨値は未検証のため、全体の既定値と同じ 25% を置く
    CatalogModel("Anzhc HeadHair seg y8n.pt", "髪", 6.5, 25),
)


def find(filename: str) -> CatalogModel | None:
    """ファイル名でカタログを引く(カタログ外のモデルなら None)"""
    return next((m for m in MODELS if m.filename == filename), None)
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_catalog.py -v`
Expected: PASS (8 件)

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/catalog.py tests/test_detect_catalog.py
git commit -m "feat(detect): 標準モデルのカタログを追加"
```

---

### Task 2: モデル別の設定の永続化

**Files:**
- Modify: `mosaic_tool/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: なし(既定値はカタログではなく呼び出し側から受け取る)
- Produces: `AppSettings` に 4 メソッド
  - `model_enabled(filename: str) -> bool` — 未登録なら `True`
  - `set_model_enabled(filename: str, value: bool) -> None`
  - `model_confidence(filename: str, minimum: int, maximum: int, default: int = DEFAULT_CONFIDENCE) -> int` — 未登録なら `default`
  - `set_model_confidence(filename: str, value: int) -> None`

`DEFAULT_CONFIDENCE = 25` は残す(カタログ外モデルの既定値として使う)。全体設定の `confidence()` / `set_confidence()` と `_KEY_CONFIDENCE` は削除する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_settings.py` の末尾に追記する:

```python
def test_model_settings_default_to_enabled_with_given_default(tmp_path):
    s = _settings(tmp_path)
    # 未登録のモデルは「有効・呼び出し側の既定値」として扱う
    assert s.model_enabled("Anzhc Eyes -seg-hd.pt") is True
    assert s.model_confidence("Anzhc Eyes -seg-hd.pt", 1, 100, 40) == 40


def test_model_confidence_falls_back_to_the_shared_default(tmp_path):
    s = _settings(tmp_path)
    assert s.model_confidence("unknown.pt", 1, 100) == DEFAULT_CONFIDENCE


def test_model_settings_roundtrip(tmp_path):
    name = "Anzhc Face seg 640 v4 y11n.pt"
    s = _settings(tmp_path)
    s.set_model_enabled(name, False)
    s.set_model_confidence(name, 33)
    s2 = _settings(tmp_path)
    assert s2.model_enabled(name) is False
    assert s2.model_confidence(name, 1, 100, 25) == 33


def test_model_settings_are_kept_per_file(tmp_path):
    s = _settings(tmp_path)
    s.set_model_enabled("a.pt", False)
    assert s.model_enabled("b.pt") is True


def test_model_confidence_out_of_range_falls_back_to_default(tmp_path):
    name = "a.pt"
    s = _settings(tmp_path)
    s.set_model_confidence(name, 500)
    assert s.model_confidence(name, 1, 100, 25) == 25
```

同ファイル冒頭の import から `DEFAULT_DEVICE` を消してはならない(既存テストが使う)。`DEFAULT_CONFIDENCE` は import 済み。

既存テストのうち全体信頼度を触るものがあれば削除する。確認コマンド:

```bash
grep -n "confidence" tests/test_settings.py tests/test_app.py
```

`tests/test_settings.py` に `s.confidence(...)` / `s.set_confidence(...)` を使う行があれば、上の新テストで置き換える形で削除する。

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `AttributeError: 'AppSettings' object has no attribute 'model_enabled'`

- [ ] **Step 3: 実装する**

`mosaic_tool/settings.py` の `_KEY_CONFIDENCE = "detect/confidence"` を次に置き換える:

```python
# モデル別設定のキー接頭辞(<接頭辞>/<ファイル名>/<項目> で 1 モデル分になる)
_KEY_MODEL_PREFIX = "detect/models"
```

`confidence()` / `set_confidence()` を次で置き換える:

```python
    def _model_key(self, filename: str, item: str) -> str:
        """モデル別設定のキー(ファイル名をそのまま含める)"""
        return f"{_KEY_MODEL_PREFIX}/{filename}/{item}"

    def model_enabled(self, filename: str) -> bool:
        """検出に使うか。未登録のモデルは有効として扱う

        ユーザーが models\\ へ置いたものは使いたくて置いたはずで、
        初期状態で無効だと「置いたのに動かない」という戸惑いを生む。
        """
        value = self._qsettings.value(self._model_key(filename, "enabled"), True)
        if isinstance(value, str):
            return value.lower() in ("true", "1")
        return bool(value)

    def set_model_enabled(self, filename: str, value: bool) -> None:
        self._qsettings.setValue(self._model_key(filename, "enabled"), bool(value))

    def model_confidence(
        self,
        filename: str,
        minimum: int,
        maximum: int,
        default: int = DEFAULT_CONFIDENCE,
    ) -> int:
        """モデルごとの信頼度しきい値 (%)。未登録ならカタログの推奨値(default)"""
        return self._int(self._model_key(filename, "confidence"), default, minimum, maximum)

    def set_model_confidence(self, filename: str, value: int) -> None:
        self._qsettings.setValue(self._model_key(filename, "confidence"), int(value))
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/settings.py tests/test_settings.py
git commit -m "feat(settings): モデルごとの有効/無効と信頼度を保存する"
```

---

### Task 3: プロトコル(リクエスト組み立てと応答の判別)

**Files:**
- Modify: `mosaic_tool/detect/convert.py`
- Test: `tests/test_detect_convert.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `build_request(image_path: str, models: dict[str, float], device: str) -> str` — 末尾に改行を付けた 1 行
  - `WorkerResponse` — `frozen=True` の dataclass。`ready: bool = False`, `progress: tuple[int, int, str] | None = None`, `detections: list[dict] | None = None`
  - `parse_response(line: str) -> WorkerResponse` — `ok: false` と壊れた JSON では `DetectError`

`detections_to_regions()` と `thin_points()` は変更しない。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_convert.py` の既存の `build_request` / `parse_response` のテストを削除し、次を追記する(既存テストの所在は `grep -n "build_request\|parse_response" tests/test_detect_convert.py` で確認する):

```python
def test_build_request_carries_per_model_confidence():
    line = convert.build_request("a.png", {"m1.pt": 0.25, "m2.pt": 0.4}, "cpu")
    assert line.endswith("\n")
    payload = json.loads(line)
    assert payload == {
        "image": "a.png",
        "models": {"m1.pt": 0.25, "m2.pt": 0.4},
        "device": "cpu",
    }


def test_build_request_keeps_non_ascii_filenames_readable():
    line = convert.build_request("画像.png", {"モデル.pt": 0.3}, "")
    assert "画像.png" in line


def test_parse_response_reports_ready():
    res = convert.parse_response(json.dumps({"ok": True, "ready": True}))
    assert res.ready is True
    assert res.detections is None
    assert res.progress is None


def test_parse_response_reports_progress():
    line = json.dumps(
        {"ok": True, "progress": {"done": 1, "total": 3, "model": "m1.pt"}}
    )
    res = convert.parse_response(line)
    assert res.progress == (1, 3, "m1.pt")
    assert res.detections is None


def test_parse_response_reports_detections():
    line = json.dumps({"ok": True, "detections": [{"bbox": [0, 0, 1, 1]}]})
    res = convert.parse_response(line)
    assert res.detections == [{"bbox": [0, 0, 1, 1]}]
    assert res.ready is False


def test_parse_response_treats_missing_detections_as_empty():
    res = convert.parse_response(json.dumps({"ok": True}))
    assert res.detections == []


def test_parse_response_raises_on_error_payload():
    with pytest.raises(convert.DetectError, match="推論に失敗"):
        convert.parse_response(json.dumps({"ok": False, "error": "推論に失敗"}))


def test_parse_response_raises_on_broken_json():
    with pytest.raises(convert.DetectError):
        convert.parse_response("{壊れた")


def test_parse_response_raises_on_non_object_payload():
    with pytest.raises(convert.DetectError):
        convert.parse_response("[1, 2, 3]")
```

`json` と `pytest` の import が無ければファイル冒頭に足す。

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_convert.py -v`
Expected: FAIL — `AttributeError: module 'mosaic_tool.detect.convert' has no attribute 'WorkerResponse'` および `build_request()` の引数不一致

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/convert.py` の `build_request()` と `parse_response()` を置き換える。冒頭の import に `from dataclasses import dataclass` を追加する。

```python
@dataclass(frozen=True)
class WorkerResponse:
    """ワーカーの応答 1 行の中身(3 種のうちどれか 1 つだけが埋まる)"""

    ready: bool = False
    progress: tuple[int, int, str] | None = None   # (完了数, 総数, モデル名)
    detections: list[dict] | None = None


def build_request(image_path: str, models: dict[str, float], device: str) -> str:
    """ワーカーへ送るリクエスト 1 行(改行付き)を組み立てる

    models はファイル名をキー、信頼度(0〜1)を値とする。
    ここに載っていないモデルはワーカー側で推論されない。
    """
    payload = {"image": image_path, "models": models, "device": device}
    return json.dumps(payload, ensure_ascii=False) + "\n"


def parse_response(line: str) -> WorkerResponse:
    """ワーカーの応答 1 行を解釈する(失敗は DetectError)"""
    try:
        payload = json.loads(line)
    except (ValueError, TypeError) as e:
        raise DetectError(f"検出結果を解釈できませんでした: {line[:200]}") from e
    if not isinstance(payload, dict):
        raise DetectError(f"検出結果の形式が不正です: {line[:200]}")
    if not payload.get("ok"):
        raise DetectError(payload.get("error") or "検出に失敗しました")
    if payload.get("ready"):
        return WorkerResponse(ready=True)
    progress = payload.get("progress")
    if isinstance(progress, dict):
        return WorkerResponse(
            progress=(
                int(progress.get("done", 0)),
                int(progress.get("total", 0)),
                str(progress.get("model", "")),
            )
        )
    return WorkerResponse(detections=payload.get("detections", []))
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_convert.py -v`
Expected: PASS

`tests/test_detect_worker_client.py` はこの時点で失敗する(Task 5 で直す)。

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/convert.py tests/test_detect_convert.py
git commit -m "feat(detect): プロトコルにモデル別信頼度と進捗を追加"
```

---

### Task 4: ワーカー側のモデル別推論と進捗出力

**Files:**
- Modify: `mosaic_tool/detect/worker_main.py`
- Test: `tests/test_detect_worker_main.py`

**Interfaces:**
- Consumes: Task 3 で決めたプロトコル(リクエストの `models`、応答の `progress`)
- Produces:
  - `detect(models: list[tuple], image_path: str, confidences: dict[str, float], device: str, on_progress=None) -> list[dict]` — `on_progress` は `(done: int, total: int, name: str)` で呼ばれる
  - `handle_request(models: list[tuple], line: str, emit) -> dict` — `emit(payload: dict)` は進捗 1 件ごとに呼ばれ、戻り値が最終応答

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_worker_main.py` の `detect` / `handle_request` を使う既存テストを次で置き換える:

```python
def test_detect_returns_bbox_and_model_name():
    model = FakeModel(FakeResult([FakeBox(0.9, [1, 2, 3, 4])]))
    result = worker_main.detect(
        [("pussyV2.pt", model)], "img.png", {"pussyV2.pt": 0.25}, ""
    )
    assert result == [
        {"model": "pussyV2.pt", "conf": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]}
    ]


def test_detect_includes_polygon_when_masks_exist():
    model = FakeModel(
        FakeResult([FakeBox(0.9, [0, 0, 10, 10])], polygons=[[(0, 0), (10, 0), (10, 10)]])
    )
    result = worker_main.detect([("m.pt", model)], "img.png", {"m.pt": 0.25}, "")
    assert result[0]["polygon"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]


def test_detect_combines_multiple_models():
    a = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    b = FakeModel(FakeResult([FakeBox(0.8, [2, 2, 3, 3])]))
    result = worker_main.detect(
        [("a.pt", a), ("b.pt", b)], "img.png", {"a.pt": 0.3, "b.pt": 0.3}, "cpu"
    )
    assert [d["model"] for d in result] == ["a.pt", "b.pt"]


def test_detect_uses_the_confidence_of_each_model():
    a = FakeModel(FakeResult([]))
    b = FakeModel(FakeResult([]))
    worker_main.detect(
        [("a.pt", a), ("b.pt", b)], "img.png", {"a.pt": 0.25, "b.pt": 0.4}, ""
    )
    assert a.calls[0]["conf"] == 0.25
    assert b.calls[0]["conf"] == 0.4


def test_detect_skips_models_not_listed():
    used = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    unused = FakeModel(FakeResult([FakeBox(0.9, [2, 2, 3, 3])]))
    result = worker_main.detect(
        [("used.pt", used), ("unused.pt", unused)], "img.png", {"used.pt": 0.3}, ""
    )
    assert unused.calls == []
    assert [d["model"] for d in result] == ["used.pt"]


def test_detect_reports_progress_per_model():
    a = FakeModel(FakeResult([]))
    b = FakeModel(FakeResult([]))
    seen = []
    worker_main.detect(
        [("a.pt", a), ("b.pt", b), ("skip.pt", FakeModel(FakeResult([])))],
        "img.png",
        {"a.pt": 0.3, "b.pt": 0.3},
        "",
        on_progress=lambda done, total, name: seen.append((done, total, name)),
    )
    # 総数は推論するモデルの件数(除外分は数えない)
    assert seen == [(1, 2, "a.pt"), (2, 2, "b.pt")]


def test_empty_device_is_passed_as_none():
    # 空文字は ultralytics へ渡さず自動選択に任せる
    model = FakeModel(FakeResult([]))
    worker_main.detect([("m.pt", model)], "img.png", {"m.pt": 0.4}, "")
    assert model.calls[0]["device"] is None


def test_handle_request_returns_error_payload_on_failure():
    class BrokenModel:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("推論に失敗")

    payload = worker_main.handle_request(
        [("m.pt", BrokenModel())],
        '{"image": "img.png", "models": {"m.pt": 0.25}}',
        lambda _payload: None,
    )
    assert payload["ok"] is False
    assert "推論に失敗" in payload["error"]


def test_handle_request_returns_detections():
    model = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    payload = worker_main.handle_request(
        [("m.pt", model)],
        '{"image": "img.png", "models": {"m.pt": 0.25}, "device": ""}',
        lambda _payload: None,
    )
    assert payload["ok"] is True
    assert len(payload["detections"]) == 1


def test_handle_request_emits_progress_payloads():
    emitted = []
    model = FakeModel(FakeResult([]))
    worker_main.handle_request(
        [("m.pt", model)],
        '{"image": "img.png", "models": {"m.pt": 0.25}}',
        emitted.append,
    )
    assert emitted == [
        {"ok": True, "progress": {"done": 1, "total": 1, "model": "m.pt"}}
    ]


def test_handle_request_without_models_returns_empty_detections():
    model = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    payload = worker_main.handle_request(
        [("m.pt", model)], '{"image": "img.png", "models": {}}', lambda _p: None
    )
    assert payload["detections"] == []
    assert model.calls == []
```

`test_worker_does_not_import_mosaic_tool` はそのまま残す。

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_worker_main.py -v`
Expected: FAIL — `detect()` の引数不一致 (`TypeError`)

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/worker_main.py` の `detect()` / `handle_request()` / `main()` を置き換える:

```python
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
```

`main()` のループを次に変える:

```python
    for line in sys.stdin:
        if line.strip():
            _emit(handle_request(models, line, _emit))
```

`DEFAULT_CONFIDENCE` は使われなくなるので削除する。

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_worker_main.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/worker_main.py tests/test_detect_worker_main.py
git commit -m "feat(detect): ワーカーがモデル別の信頼度と進捗を扱えるようにする"
```

---

### Task 5: クライアント側の進捗シグナル

**Files:**
- Modify: `mosaic_tool/detect/worker_client.py`
- Test: `tests/test_detect_worker_client.py`

**Interfaces:**
- Consumes: `convert.build_request(image_path, models, device)`, `convert.parse_response(line) -> WorkerResponse`
- Produces: `DetectWorker`
  - `progress = Signal(int, int, str)` — (完了数, 総数, モデル名)
  - `request(self, image_path: str, models: dict[str, float], device: str) -> None`
  - 既存の `detected = Signal(list)` / `failed = Signal(str)` / `is_busy()` / `stop()` は変更しない

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_worker_client.py` の `_feed` を使うテスト群を次で置き換える:

```python
def test_feed_emits_detections_for_complete_line(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(json.dumps({"ok": True, "detections": [{"bbox": [0, 0, 1, 1]}]}) + "\n")
    assert received == [[{"bbox": [0, 0, 1, 1]}]]


def test_feed_waits_for_the_newline(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    payload = json.dumps({"ok": True, "detections": []})
    worker._feed(payload[:10])
    assert received == []
    worker._feed(payload[10:] + "\n")
    assert received == [[]]


def test_feed_emits_failure_for_error_response(qapp):
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(json.dumps({"ok": False, "error": "推論に失敗"}) + "\n")
    assert errors and "推論に失敗" in errors[0]


def test_ready_line_is_not_reported_as_detection(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    assert received == []


def test_startup_error_is_reported_as_failure(qapp):
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker._feed(json.dumps({"ok": False, "error": "モデルの読み込みに失敗しました"}) + "\n")
    assert errors and "モデルの読み込み" in errors[0]


def test_feed_emits_progress(qapp):
    worker = _worker(qapp)
    seen = []
    worker.progress.connect(lambda done, total, name: seen.append((done, total, name)))
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(
        json.dumps({"ok": True, "progress": {"done": 1, "total": 2, "model": "a.pt"}})
        + "\n"
    )
    assert seen == [(1, 2, "a.pt")]


def test_progress_is_not_reported_as_detection(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(
        json.dumps({"ok": True, "progress": {"done": 1, "total": 1, "model": "a.pt"}})
        + "\n"
    )
    assert received == []


def test_detections_arrive_even_without_a_ready_line(qapp):
    # 応答は種別で判別するため、ready の有無に依存しない
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "detections": []}) + "\n")
    assert received == [[]]


def test_request_without_models_reports_failure(qapp):
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker.request("img.png", {}, "")
    assert errors and "モデル" in errors[0]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_worker_client.py -v`
Expected: FAIL — `AttributeError: 'DetectWorker' object has no attribute 'progress'`

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/worker_client.py` を次のように変える。

シグナル定義に 1 行足す:

```python
    detected = Signal(list)              # 検出結果 (list[dict])
    progress = Signal(int, int, str)     # (完了数, 総数, モデル名)
    failed = Signal(str)                 # エラーメッセージ
```

`request()` を置き換える:

```python
    def request(self, image_path: str, models: dict, device: str) -> None:
        """検出を依頼する(結果は detected / failed で返る)

        models はファイル名をキー、信頼度(0〜1)を値とする。
        ワーカーは models\\ を全件読み込むが、推論するのはここに載せたものだけ。
        """
        if self._busy:
            return
        if not models:
            self.failed.emit("有効な検出モデルがありません")
            return
        available = paths.model_files()
        if not available:
            self.failed.emit(f"検出モデルが見つかりません: {paths.models_dir()}")
            return
        if self._process is None and not self._start(available):
            return
        self._busy = True
        self._timer.start()
        self._process.write(build_request(image_path, models, device).encode("utf-8"))
```

`_handle_line()` を置き換える:

```python
    def _handle_line(self, line: str) -> None:
        try:
            response = parse_response(line)
        except DetectError as e:
            self._finish_request()
            self.failed.emit(str(e))
            return
        if response.ready:
            # 起動直後のモデル読み込み完了通知。待っている呼び出し元は無い
            return
        if response.progress is not None:
            self.progress.emit(*response.progress)
            return
        self._finish_request()
        self.detected.emit(response.detections or [])
```

`self._ready` は使わなくなるため、`__init__` / `stop()` / `_start()` / `_on_process_finished()` から削除する。

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_worker_client.py -v`
Expected: PASS

この時点で `tests/test_app.py` が失敗する(Task 10 で直す)。他は通ることを確認する:

Run: `python -m pytest tests/ -v --ignore=tests/test_app.py`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/worker_client.py tests/test_detect_worker_client.py
git commit -m "feat(detect): 検出の進捗をシグナルで通知する"
```

---

### Task 6: 一括追加分を非選択にする

**Files:**
- Modify: `mosaic_tool/canvas.py:608-621`
- Test: `tests/test_canvas.py`

**Interfaces:**
- Consumes: なし
- Produces: `MosaicCanvas.add_regions()` の戻り値と undo の挙動は変更なし。追加した item が選択状態にならない点だけが変わる

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_canvas.py` に追記する(既存に `add_regions` のテストがあれば `grep -n "add_regions" tests/test_canvas.py` で確認し、選択状態を期待している行は削除する):

```python
def test_add_regions_leaves_items_unselected(canvas):
    # 自動検出の追加分は非選択。意図せず全体を動かしてしまう事故を防ぐ
    regions = [
        Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10)),
        Region(kind=RegionKind.RECT, rect=QRectF(20, 20, 10, 10)),
    ]
    items = canvas.add_regions(regions)
    assert items and all(not item.isSelected() for item in items)


def test_add_region_keeps_selection_for_manual_drawing(canvas):
    # 手描き直後の選択はそのまま(描いてすぐ変形できるようにする)
    item = canvas.add_region(Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10)))
    item.setSelected(True)
    assert item.isSelected()
```

`canvas` フィクスチャ・`Region` / `RegionKind` / `QRectF` の import は既存ファイルの書き方に合わせる。画像が読み込まれていないと範囲を追加できない場合は、既存テストと同じ手順で画像を設定してから呼ぶ。

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_canvas.py -v -k add_region`
Expected: FAIL — `test_add_regions_leaves_items_unselected` が `assert` で落ちる

- [ ] **Step 3: 実装する**

`mosaic_tool/canvas.py` の `add_regions()` を次に置き換える:

```python
    def add_regions(self, regions: list[Region]) -> list[RegionItem]:
        """複数の範囲をまとめて追加する(自動検出用)

        Undo スタックには 1 エントリだけ積み、Ctrl+Z 一回で追加分をまとめて
        取り消せるようにする。追加分は非選択のまま置く(選択したままだと
        次の操作で自動検出の結果を丸ごと動かしてしまう)。
        """
        if not regions:
            return []
        self._scene.clearSelection()
        items = [self.add_region(region, push_undo=False) for region in regions]
        self._undo_stack.append(("add_many", items))
        return items
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_canvas.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/canvas.py tests/test_canvas.py
git commit -m "fix(canvas): 自動検出で追加した範囲を非選択にする"
```

---

### Task 7: モデルのダウンロード

**Files:**
- Create: `mosaic_tool/detect/downloader.py`
- Test: `tests/test_detect_downloader.py`

**Interfaces:**
- Consumes: `catalog.MODELS`, `catalog.CatalogModel`, `paths.models_dir()`
- Produces:
  - `pending_models() -> list[CatalogModel]` — `models\` にまだ無い標準モデル
  - `part_path(destination: Path) -> Path` — 書き込み中の一時ファイル名(`<名前>.pt.part`)
  - `ModelDownloader(QObject)`
    - `progress = Signal(int, int)` — (受信バイト, 全体バイト。不明なら 0)
    - `finished = Signal(bool, str)` — (成功したか, メッセージ)
    - `start(self, url: str, destination: Path) -> None`
    - `cancel(self) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_downloader.py`:

```python
"""ダウンロード対象の判定と一時ファイル名の検証(通信は行わない)"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect import catalog, downloader, paths  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_pending_models_lists_all_when_nothing_is_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert downloader.pending_models() == list(catalog.MODELS)


def test_pending_models_skips_installed_files(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / catalog.MODELS[0].filename).write_bytes(b"x")
    pending = downloader.pending_models()
    assert catalog.MODELS[0] not in pending
    assert len(pending) == len(catalog.MODELS) - 1


def test_part_path_appends_suffix():
    assert downloader.part_path(Path("C:/m/a.pt")) == Path("C:/m/a.pt.part")


def test_cancel_before_start_does_nothing(qapp):
    # 何も起きていない状態で呼んでも例外にならない
    downloader.ModelDownloader().cancel()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_downloader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mosaic_tool.detect.downloader'`

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/downloader.py`:

```python
"""標準モデルのダウンロード(QtNetwork を使い、本体に依存を増やさない)"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from mosaic_tool.detect import paths
from mosaic_tool.detect.catalog import MODELS, CatalogModel

PART_SUFFIX = ".part"


def pending_models() -> list[CatalogModel]:
    """models\\ にまだ置かれていない標準モデル"""
    directory = paths.models_dir()
    return [m for m in MODELS if not (directory / m.filename).is_file()]


def part_path(destination: Path) -> Path:
    """書き込み中の一時ファイル名

    中断したファイルが .pt として一覧に現れ、壊れたモデルとして
    読み込みに失敗するのを防ぐ。
    """
    return destination.with_name(destination.name + PART_SUFFIX)


class ModelDownloader(QObject):
    """1 ファイルのダウンロード(非同期)"""

    progress = Signal(int, int)      # (受信バイト, 全体バイト。不明なら 0)
    finished = Signal(bool, str)     # (成功したか, メッセージ)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._file = None
        self._destination: Path | None = None
        self._cancelled = False

    def start(self, url: str, destination: Path) -> None:
        """url を destination へ保存する(結果は finished で返る)"""
        self._cancelled = False
        self._destination = destination
        part = part_path(destination)
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
            self._file = part.open("wb")
        except OSError as e:
            self.finished.emit(False, f"保存先を開けません: {part}\n{e}")
            return
        request = QNetworkRequest(QUrl(url))
        # HuggingFace は CDN へリダイレクトするため追従が要る
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self._manager.get(request)
        reply.readyRead.connect(self._on_ready_read)
        reply.downloadProgress.connect(self.progress.emit)
        reply.finished.connect(self._on_finished)
        self._reply = reply

    def cancel(self) -> None:
        self._cancelled = True
        if self._reply is not None:
            self._reply.abort()

    def _on_ready_read(self) -> None:
        if self._reply is not None and self._file is not None:
            self._file.write(bytes(self._reply.readAll()))

    def _close_file(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _discard(self) -> None:
        """書きかけを残さない"""
        self._close_file()
        if self._destination is not None:
            part_path(self._destination).unlink(missing_ok=True)

    def _on_finished(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        error = reply.error()
        reply.deleteLater()
        if self._cancelled:
            self._discard()
            self.finished.emit(False, "ダウンロードを中止しました")
            return
        if error != QNetworkReply.NetworkError.NoError:
            message = reply.errorString()
            self._discard()
            self.finished.emit(False, f"ダウンロードに失敗しました: {message}")
            return
        self._close_file()
        destination = self._destination
        try:
            part_path(destination).replace(destination)
        except OSError as e:
            self._discard()
            self.finished.emit(False, f"保存に失敗しました: {destination}\n{e}")
            return
        self.finished.emit(True, f"取得しました: {destination.name}")
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_downloader.py -v`
Expected: PASS

実通信の確認は Task 8 のあとに手動で行う。

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/downloader.py tests/test_detect_downloader.py
git commit -m "feat(detect): 標準モデルのダウンロード処理を追加"
```

---

### Task 8: セットアップで標準モデルを自動取得する

**Files:**
- Modify: `mosaic_tool/detect/setup_dialog.py`
- Test: `tests/test_detect_setup_dialog.py` (新規)

**Interfaces:**
- Consumes: `RuntimeInstaller`, `has_nvidia_gpu()`, `downloader.pending_models()`, `downloader.ModelDownloader`, `paths.models_dir()`
- Produces: `RuntimeSetupDialog` — 外から見た使い方(`exec()` して `Accepted` なら準備完了)は変えない

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_setup_dialog.py`:

```python
"""セットアップダイアログの既定選択とモデル取得の進行の検証"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from mosaic_tool.detect import downloader, paths, setup_dialog  # noqa: E402
from mosaic_tool.detect.catalog import CatalogModel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeDownloader:
    """start() を呼ばれても通信せず、テストから結果を流し込めるようにする"""

    def __init__(self, parent=None):
        self.calls = []

    def start(self, url, destination):
        self.calls.append((url, destination))

    def cancel(self):
        pass


def test_cpu_is_selected_by_default(qapp, monkeypatch):
    # GPU があると判定される環境でも既定は CPU
    monkeypatch.setattr(setup_dialog, "has_nvidia_gpu", lambda: True)
    dialog = setup_dialog.RuntimeSetupDialog()
    assert dialog._cpu_radio.isChecked() is True
    assert dialog._gpu_radio.isChecked() is False


def test_model_download_starts_after_the_runtime_is_built(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    pending = [CatalogModel("a.pt", "顔", 1.0, 25), CatalogModel("b.pt", "目", 1.0, 40)]
    monkeypatch.setattr(downloader, "pending_models", lambda: pending)
    fake = FakeDownloader()
    monkeypatch.setattr(setup_dialog, "ModelDownloader", lambda parent=None: fake)
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    assert fake.calls and fake.calls[0][1].name == "a.pt"


def test_all_models_are_downloaded_in_order(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    pending = [CatalogModel("a.pt", "顔", 1.0, 25), CatalogModel("b.pt", "目", 1.0, 40)]
    monkeypatch.setattr(downloader, "pending_models", lambda: pending)
    fake = FakeDownloader()
    monkeypatch.setattr(setup_dialog, "ModelDownloader", lambda parent=None: fake)
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    dialog._on_download_finished(True, "取得しました: a.pt")
    assert [c[1].name for c in fake.calls] == ["a.pt", "b.pt"]
    dialog._on_download_finished(True, "取得しました: b.pt")
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_download_failure_still_completes_the_setup(qapp, monkeypatch, tmp_path):
    # venv さえあれば手動でモデルを置いて使えるため、ここで失敗にしない
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(
        downloader, "pending_models", lambda: [CatalogModel("a.pt", "顔", 1.0, 25)]
    )
    monkeypatch.setattr(
        setup_dialog, "ModelDownloader", lambda parent=None: FakeDownloader()
    )
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    dialog._on_download_finished(False, "ダウンロードに失敗しました: 通信エラー")
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert "失敗" in dialog._log.toPlainText()


def test_runtime_failure_does_not_start_downloads(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(
        downloader, "pending_models", lambda: [CatalogModel("a.pt", "顔", 1.0, 25)]
    )
    fake = FakeDownloader()
    monkeypatch.setattr(setup_dialog, "ModelDownloader", lambda parent=None: fake)
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(False, "セットアップに失敗しました")
    assert fake.calls == []
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_setup_accepts_immediately_when_no_model_is_pending(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "pending_models", lambda: [])
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    assert dialog.result() == QDialog.DialogCode.Accepted
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_setup_dialog.py -v`
Expected: FAIL — `AttributeError: module 'mosaic_tool.detect.setup_dialog' has no attribute 'ModelDownloader'`

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/setup_dialog.py` を全面的に書き換える:

```python
"""推論環境のセットアップ用ダイアログ(GPU/CPU の選択、進捗表示、標準モデルの取得)"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QVBoxLayout,
)

from mosaic_tool.detect import downloader, paths
from mosaic_tool.detect.catalog import CatalogModel
from mosaic_tool.detect.downloader import ModelDownloader
from mosaic_tool.detect.runtime import RuntimeInstaller, has_nvidia_gpu

INTRO = (
    "自動検出を使うには、推論用の実行環境を用意する必要があります。\n"
    "ダウンロードには時間がかかります(回線状況により数分〜十数分)。\n"
    "続けて標準の検出モデル(顔・目・髪 / 合計 約 20MB)を取得します。"
)
GPU_LABEL = "GPU を使う (NVIDIA / ダウンロード 約 2.5GB / 検出が速い)"
GPU_DETECTED_NOTE = " ※NVIDIA GPU を検出しました"
CPU_LABEL = "CPU のみ (ダウンロード 約 250MB / どの環境でも動く)"


class RuntimeSetupDialog(QDialog):
    """セットアップの選択と実行。完了すると accept() する

    venv を構築したあと、まだ置かれていない標準モデルを順に取得する。
    モデルの取得に失敗しても venv があれば手動でモデルを置いて使えるため、
    セットアップ自体は成功として扱う。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自動検出のセットアップ")
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(INTRO))
        gpu_label = GPU_LABEL + (GPU_DETECTED_NOTE if has_nvidia_gpu() else "")
        self._gpu_radio = QRadioButton(gpu_label)
        self._cpu_radio = QRadioButton(CPU_LABEL)
        # 既定は常に CPU。GPU は容量が大きいため明示的に選んでもらう
        self._cpu_radio.setChecked(True)
        layout.addWidget(self._gpu_radio)
        layout.addWidget(self._cpu_radio)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)
        self._bar = QProgressBar()
        self._bar.setVisible(False)
        layout.addWidget(self._bar)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始")
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self._cancel)
        layout.addWidget(self._buttons)

        self._installer = RuntimeInstaller(self)
        self._installer.progress.connect(self._log.appendPlainText)
        self._installer.finished.connect(self._on_runtime_finished)
        self._downloader = ModelDownloader(self)
        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.finished.connect(self._on_download_finished)
        self._queue: list[CatalogModel] = []
        self._total = 0
        self._running = False

    def _start(self) -> None:
        self._running = True
        self._set_inputs_enabled(False)
        self._installer.start(use_gpu=self._gpu_radio.isChecked())

    def _cancel(self) -> None:
        if self._running:
            self._installer.cancel()
            self._downloader.cancel()
            return
        self.reject()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._gpu_radio.setEnabled(enabled)
        self._cpu_radio.setEnabled(enabled)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _on_runtime_finished(self, ok: bool, message: str) -> None:
        self._log.appendPlainText(message)
        if not ok:
            self._running = False
            self._bar.setVisible(False)
            self._set_inputs_enabled(True)
            return
        self._queue = list(downloader.pending_models())
        self._total = len(self._queue)
        self._start_next_download()

    def _start_next_download(self) -> None:
        if not self._queue:
            self._running = False
            self._bar.setVisible(False)
            self.accept()
            return
        model = self._queue[0]
        done = self._total - len(self._queue) + 1
        self._log.appendPlainText(
            f"モデルを取得中: {model.filename} ({done}/{self._total})"
        )
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)  # 全体サイズが分かるまでは不確定表示
        paths.models_dir().mkdir(parents=True, exist_ok=True)
        self._downloader.start(model.url, paths.models_dir() / model.filename)

    def _on_download_progress(self, received: int, total: int) -> None:
        if total <= 0:
            return
        self._bar.setRange(0, total)
        self._bar.setValue(received)

    def _on_download_finished(self, ok: bool, message: str) -> None:
        self._log.appendPlainText(message)
        if self._queue:
            self._queue.pop(0)
        # 取得に失敗しても続行する(次回のセットアップで再試行される)
        self._start_next_download()
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_setup_dialog.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/setup_dialog.py tests/test_detect_setup_dialog.py
git commit -m "feat(detect): セットアップで標準モデルを自動取得する"
```

---

### Task 9: 自動検出ウィンドウ

**Files:**
- Create: `mosaic_tool/detect/detect_window.py`
- Test: `tests/test_detect_window.py`

**Interfaces:**
- Consumes: `AppSettings.model_enabled/set_model_enabled/model_confidence/set_model_confidence`, `catalog.find()`, `paths.model_files()`, `paths.models_dir()`, `paths.is_runtime_ready()`, `RuntimeSetupDialog`
- Produces: `DetectWindow(QDialog)`
  - `detect_requested = Signal(dict)` — `{ファイル名: 信頼度(0.0〜1.0)}`
  - `models_changed = Signal()` — `models\` の顔ぶれが変わった(ワーカーの再起動が要る)
  - `__init__(self, settings: AppSettings, parent=None)`
  - `refresh(self) -> None` — 環境の状態とモデル一覧を作り直す
  - `set_image_available(self, available: bool) -> None` — メインウィンドウに画像があるか
  - `set_running(self, running: bool) -> None` — 検出中の入力制御
  - `set_progress(self, done: int, total: int) -> None`
  - `enabled_models(self) -> dict[str, float]`
  - 定数 `CONFIDENCE_MIN = 1` / `CONFIDENCE_MAX = 100`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_window.py`:

```python
"""自動検出ウィンドウのモデル一覧・設定連動・実行可否の検証"""
import os

import pytest
from PySide6.QtCore import QSettings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect import paths  # noqa: E402
from mosaic_tool.detect.detect_window import DetectWindow  # noqa: E402
from mosaic_tool.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    """models/ と runtime/ を持つ一時ディレクトリを基準にしたウィンドウ"""
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "Anzhc Eyes -seg-hd.pt").write_bytes(b"x")
    (models / "unknown.pt").write_bytes(b"x")
    (tmp_path / "runtime" / "Scripts").mkdir(parents=True)
    (tmp_path / "runtime" / "Scripts" / "python.exe").write_bytes(b"x")
    settings = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    win = DetectWindow(settings)
    win.set_image_available(True)
    yield win
    win.close()


def test_lists_models_from_the_models_directory(window):
    assert set(window._rows) == {"Anzhc Eyes -seg-hd.pt", "unknown.pt"}


def test_catalog_model_uses_its_recommended_confidence(window):
    assert window._rows["Anzhc Eyes -seg-hd.pt"].slider.value() == 40


def test_unknown_model_falls_back_to_the_shared_default(window):
    assert window._rows["unknown.pt"].slider.value() == 25


def test_catalog_model_shows_its_label(window):
    assert window._rows["Anzhc Eyes -seg-hd.pt"].label.text() == "目"
    assert window._rows["unknown.pt"].label.text() == ""


def test_enabled_models_returns_confidence_as_ratio(window):
    window._rows["unknown.pt"].check.setChecked(False)
    assert window.enabled_models() == {"Anzhc Eyes -seg-hd.pt": 0.4}


def test_unchecking_disables_the_slider(window):
    row = window._rows["unknown.pt"]
    row.check.setChecked(False)
    assert row.slider.isEnabled() is False


def test_confidence_change_is_persisted(window, tmp_path):
    window._rows["unknown.pt"].slider.setValue(70)
    reopened = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    assert reopened.model_confidence("unknown.pt", 1, 100) == 70


def test_enabled_state_is_persisted(window, tmp_path):
    window._rows["Anzhc Eyes -seg-hd.pt"].check.setChecked(False)
    reopened = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    assert reopened.model_enabled("Anzhc Eyes -seg-hd.pt") is False


def test_detect_button_is_disabled_without_any_enabled_model(window):
    for row in window._rows.values():
        row.check.setChecked(False)
    assert window._detect_button.isEnabled() is False


def test_detect_button_is_disabled_without_an_image(window):
    window.set_image_available(False)
    assert window._detect_button.isEnabled() is False


def test_detect_requested_carries_the_enabled_models(window):
    window._rows["unknown.pt"].check.setChecked(False)
    received = []
    window.detect_requested.connect(received.append)
    window._on_detect_clicked()
    assert received == [{"Anzhc Eyes -seg-hd.pt": 0.4}]


def test_running_state_disables_the_detect_button(window):
    window.set_running(True)
    assert window._detect_button.isEnabled() is False
    window.set_running(False)
    assert window._detect_button.isEnabled() is True


def test_progress_bar_reflects_the_reported_counts(window):
    window.set_progress(1, 3)
    # ウィンドウを表示していないため isVisible() ではなく isVisibleTo() で見る
    assert window._bar.isVisibleTo(window) is True
    assert (window._bar.value(), window._bar.maximum()) == (1, 3)


def test_progress_bar_is_hidden_when_the_run_ends(window):
    window.set_progress(1, 3)
    window.set_running(False)
    assert window._bar.isVisibleTo(window) is False


def test_refresh_picks_up_new_files(window, tmp_path):
    (tmp_path / "models" / "new.pt").write_bytes(b"x")
    window.refresh()
    assert "new.pt" in window._rows


def test_refresh_emits_models_changed_only_when_files_change(window, tmp_path):
    seen = []
    window.models_changed.connect(lambda: seen.append(1))
    window.refresh()
    assert seen == []
    (tmp_path / "models" / "new.pt").write_bytes(b"x")
    window.refresh()
    assert seen == [1]


def test_runtime_missing_disables_the_model_area(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "models").mkdir()
    settings = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    win = DetectWindow(settings)
    win.set_image_available(True)
    assert win._detect_button.isEnabled() is False
    assert "未構築" in win._runtime_label.text()
    win.close()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_detect_window.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mosaic_tool.detect.detect_window'`

- [ ] **Step 3: 実装する**

`mosaic_tool/detect/detect_window.py`:

```python
"""自動検出ウィンドウ: 推論環境の状態、モデルごとの設定、検出の実行と進捗"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mosaic_tool.detect import catalog, paths
from mosaic_tool.detect.setup_dialog import RuntimeSetupDialog
from mosaic_tool.settings import AppSettings

CONFIDENCE_MIN = 1    # 信頼度しきい値の下限 (%)
CONFIDENCE_MAX = 100  # 同上限 (%)
READY_TEXT = "推論環境: 構築済み"
NOT_READY_TEXT = "推論環境: 未構築"
NO_MODEL_TEXT = (
    "モデルがありません。セットアップすると標準モデルが取得されます。\n"
    "自分で用意した .pt は models フォルダへ置いて「更新」を押してください。"
)


@dataclass
class ModelRow:
    """モデル 1 件分のウィジェット"""

    check: QCheckBox
    label: QLabel
    slider: QSlider
    value_label: QLabel


class DetectWindow(QDialog):
    """自動検出の操作をまとめたモードレスウィンドウ

    検出の実行自体は持たず、対象モデルを detect_requested で伝える。
    結果の反映(範囲の追加)はメインウィンドウ側の責務。
    """

    detect_requested = Signal(dict)   # {ファイル名: 信頼度(0.0〜1.0)}
    models_changed = Signal()         # models/ の顔ぶれが変わった

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._rows: dict[str, ModelRow] = {}
        self._filenames: list[str] = []
        self._image_available = False
        self._running = False
        self.setWindowTitle("自動検出")
        self.setModal(False)
        self.resize(620, 460)

        layout = QVBoxLayout(self)
        runtime_row = QHBoxLayout()
        self._runtime_label = QLabel()
        runtime_row.addWidget(self._runtime_label)
        runtime_row.addStretch()
        self._setup_button = QPushButton()
        self._setup_button.clicked.connect(self._on_setup_clicked)
        runtime_row.addWidget(self._setup_button)
        layout.addLayout(runtime_row)

        group = QGroupBox("モデル")
        group_layout = QVBoxLayout(group)
        header = QHBoxLayout()
        header.addStretch()
        open_button = QPushButton("フォルダを開く")
        open_button.clicked.connect(self._on_open_folder)
        header.addWidget(open_button)
        refresh_button = QPushButton("更新")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        group_layout.addLayout(header)
        self._empty_label = QLabel(NO_MODEL_TEXT)
        self._empty_label.setWordWrap(True)
        group_layout.addWidget(self._empty_label)
        # 行数が増えてもウィンドウが伸び続けないようスクロールさせる
        self._rows_widget = QWidget()
        self._grid = QGridLayout(self._rows_widget)
        self._grid.setColumnStretch(2, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_widget)
        group_layout.addWidget(scroll)
        layout.addWidget(group, stretch=1)

        self._bar = QProgressBar()
        self._bar.setVisible(False)
        layout.addWidget(self._bar)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._detect_button = QPushButton("検出実行")
        self._detect_button.clicked.connect(self._on_detect_clicked)
        buttons.addWidget(self._detect_button)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.refresh()

    # --- 状態の反映 ---

    def refresh(self) -> None:
        """推論環境の状態とモデル一覧を作り直す"""
        ready = paths.is_runtime_ready()
        self._runtime_label.setText(READY_TEXT if ready else NOT_READY_TEXT)
        self._setup_button.setText("再セットアップ" if ready else "セットアップ")
        filenames = [p.name for p in paths.model_files()]
        changed = filenames != self._filenames
        if changed:
            self._filenames = filenames
            self._rebuild_rows()
            # 一覧の顔ぶれが変わったらワーカーは古い構成のままなので伝える
            self.models_changed.emit()
        self._empty_label.setVisible(not filenames)
        self._update_detect_enabled()

    def _rebuild_rows(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = {}
        for row, filename in enumerate(self._filenames):
            entry = catalog.find(filename)
            # 初期値はカタログの推奨値。カタログ外のモデルは全体の既定値を使う
            default = entry.confidence if entry else DEFAULT_CONFIDENCE
            check = QCheckBox(filename)
            check.setChecked(self._settings.model_enabled(filename))
            label = QLabel(entry.label if entry else "")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(CONFIDENCE_MIN, CONFIDENCE_MAX)
            slider.setValue(
                self._settings.model_confidence(
                    filename, CONFIDENCE_MIN, CONFIDENCE_MAX, default
                )
            )
            value_label = QLabel(f"{slider.value()} %")
            check.toggled.connect(
                lambda checked, name=filename: self._on_enabled_changed(name, checked)
            )
            slider.valueChanged.connect(
                lambda value, name=filename: self._on_confidence_changed(name, value)
            )
            for column, widget in enumerate((check, label, slider, value_label)):
                self._grid.addWidget(widget, row, column)
            self._rows[filename] = ModelRow(check, label, slider, value_label)
            self._apply_row_enabled(filename)

    def _apply_row_enabled(self, filename: str) -> None:
        row = self._rows[filename]
        enabled = row.check.isChecked()
        row.slider.setEnabled(enabled)
        row.value_label.setEnabled(enabled)

    def set_image_available(self, available: bool) -> None:
        """メインウィンドウに編集中の画像があるか"""
        self._image_available = available
        self._update_detect_enabled()

    def set_running(self, running: bool) -> None:
        """検出中は実行ボタンを止め、終わったらプログレスを隠す"""
        self._running = running
        if not running:
            self._bar.setVisible(False)
        self._update_detect_enabled()

    def set_progress(self, done: int, total: int) -> None:
        self._bar.setVisible(True)
        self._bar.setRange(0, max(total, 1))
        self._bar.setValue(done)

    def enabled_models(self) -> dict:
        """有効なモデルとその信頼度(0.0〜1.0)"""
        return {
            name: row.slider.value() / 100
            for name, row in self._rows.items()
            if row.check.isChecked()
        }

    def _update_detect_enabled(self) -> None:
        self._detect_button.setEnabled(
            paths.is_runtime_ready()
            and self._image_available
            and not self._running
            and bool(self.enabled_models())
        )

    # --- 操作 ---

    def _on_enabled_changed(self, filename: str, checked: bool) -> None:
        self._settings.set_model_enabled(filename, checked)
        self._apply_row_enabled(filename)
        self._update_detect_enabled()

    def _on_confidence_changed(self, filename: str, value: int) -> None:
        self._settings.set_model_confidence(filename, value)
        self._rows[filename].value_label.setText(f"{value} %")

    def _on_setup_clicked(self) -> None:
        RuntimeSetupDialog(self).exec()
        self.refresh()

    def _on_open_folder(self) -> None:
        directory = paths.models_dir()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _on_detect_clicked(self) -> None:
        models = self.enabled_models()
        if not models:
            return
        self.set_progress(0, len(models))
        self.detect_requested.emit(models)
```

冒頭の import は `from mosaic_tool.settings import DEFAULT_CONFIDENCE, AppSettings` とする。

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_detect_window.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/detect/detect_window.py tests/test_detect_window.py
git commit -m "feat(detect): 自動検出ウィンドウを追加"
```

---

### Task 10: メインウィンドウとの配線

**Files:**
- Modify: `mosaic_tool/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `DetectWindow`(`detect_requested` / `models_changed` / `refresh()` / `set_image_available()` / `set_running()` / `set_progress()`), `DetectWorker.request(image, models, device)`, `DetectWorker.progress`
- Produces: `MainWindow`
  - `_open_detect_window(self) -> None` — ウィンドウを作って(あれば前面に出して)表示する
  - `_start_detect(self, models: dict) -> None` — 検出を依頼する
  - `_detect_window: DetectWindow | None`

削除するもの: `_confidence_spin`、`CONFIDENCE_MIN` / `CONFIDENCE_MAX` / `CONFIDENCE_STEP`、`_warn_models_missing()`、`_on_detect()`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_app.py` を確認し、`_confidence_spin` / `CONFIDENCE_*` / `_on_detect` を参照するテストがあれば削除する:

```bash
grep -n "confidence\|CONFIDENCE\|_on_detect\|検出" tests/test_app.py
```

そのうえで追記する:

```python
def test_toolbar_has_no_confidence_spinbox(window):
    # 信頼度はモデルごとの設定に一本化した
    assert not hasattr(window, "_confidence_spin")


def test_detect_action_opens_the_window(window):
    window._detect_act.trigger()
    assert window._detect_window is not None
    assert window._detect_window.isVisible() is True
    window._detect_window.close()


def test_detect_window_is_reused(window):
    window._detect_act.trigger()
    first = window._detect_window
    window._detect_act.trigger()
    assert window._detect_window is first
    first.close()


def test_detect_window_learns_whether_an_image_is_open(window):
    window._detect_act.trigger()
    # フィクスチャは画像 2 枚を開いた状態
    assert window._detect_window._image_available is True
    window._detect_window.close()


def test_start_detect_sends_the_models_to_the_worker(window, monkeypatch):
    sent = []
    monkeypatch.setattr(
        window._worker,
        "request",
        lambda image, models, device: sent.append((image, models, device)),
    )
    window._detect_act.trigger()
    window._start_detect({"a.pt": 0.25})
    assert sent and sent[0][1] == {"a.pt": 0.25}
    window._detect_window.close()


def test_detect_failure_restores_the_window(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    window._detect_act.trigger()
    window._detect_window.set_running(True)
    window._on_detect_failed("検出に失敗しました")
    assert window._detect_window._running is False
    window._detect_window.close()


def test_closing_the_main_window_closes_the_detect_window(window):
    window._detect_act.trigger()
    detect_window = window._detect_window
    window.close()
    assert detect_window.isVisible() is False
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute '_detect_window'`

- [ ] **Step 3: 実装する**

`mosaic_tool/app.py` を次のように変える。

import を差し替える(`QDialog` / `QDesktopServices` / `QUrl` / `detect_paths` が不要になる場合は削除する。`detect_paths` は使わなくなる):

```python
from mosaic_tool.detect.convert import detections_to_regions
from mosaic_tool.detect.detect_window import DetectWindow
from mosaic_tool.detect.worker_client import DetectWorker
```

定数 `CONFIDENCE_MIN` / `CONFIDENCE_MAX` / `CONFIDENCE_STEP` を削除する。

`__init__` のワーカー設定に 1 行足し、ウィンドウの保持を追加する:

```python
        # 自動検出(推論は別プロセスの venv 側で動く)
        self._worker = DetectWorker(self)
        self._worker.detected.connect(self._on_detected)
        self._worker.progress.connect(self._on_detect_progress)
        self._worker.failed.connect(self._on_detect_failed)
        self._detect_window: DetectWindow | None = None
```

`_build_toolbar()` の末尾(自動検出まわり)を次に置き換える:

```python
        # 自動検出: 専用ウィンドウでモデルと信頼度を選んでから実行する
        self._detect_act = QAction("自動検出", self)
        self._add_shortcut(self._detect_act, QKeySequence(Qt.Key.Key_D))
        self._detect_act.triggered.connect(self._open_detect_window)
        tb.addAction(self._detect_act)
```

`--- 自動検出 ---` 節を次で置き換える:

```python
    def _open_detect_window(self) -> None:
        """自動検出ウィンドウを開く(2 回目以降は前面に出す)"""
        if self._detect_window is None:
            window = DetectWindow(self._settings, self)
            window.detect_requested.connect(self._start_detect)
            # モデルの顔ぶれが変わったらワーカーを畳み、次回に新しい構成で起動させる
            window.models_changed.connect(self._worker.stop)
            self._detect_window = window
        self._detect_window.set_image_available(bool(self._images))
        self._detect_window.refresh()
        self._detect_window.show()
        self._detect_window.raise_()
        self._detect_window.activateWindow()

    def _start_detect(self, models: dict) -> None:
        """表示中の画像に対して自動検出を実行する"""
        if not self._images or self._current_image is None or self._worker.is_busy():
            return
        if self._detect_window is not None:
            self._detect_window.set_running(True)
        self.statusBar().showMessage("検出中...")
        self._worker.request(
            str(self._images[self._index]),
            models,
            "" if self._settings.device() == "auto" else "cpu",
        )

    def _on_detect_progress(self, done: int, total: int, _model: str) -> None:
        if self._detect_window is not None:
            self._detect_window.set_progress(done, total)

    def _finish_detect(self) -> None:
        if self._detect_window is not None:
            self._detect_window.set_running(False)

    def _on_detected(self, detections: list) -> None:
        """検出結果を範囲として追加する(既存の範囲は残す)"""
        self._finish_detect()
        if self._current_image is None:
            return
        regions = detections_to_regions(detections, self._current_image.size)
        if not regions:
            self.statusBar().showMessage("検出されませんでした", 5000)
            return
        self.canvas.add_regions(regions)
        self.statusBar().showMessage(f"{len(regions)} 件の範囲を追加しました", 5000)

    def _on_detect_failed(self, message: str) -> None:
        self._finish_detect()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "検出エラー", message)
```

`_load_current()` の末尾(画像を読み込んだ後)と、画像が無くなる分岐の両方に、ウィンドウへの通知を足す:

```python
        if self._detect_window is not None:
            self._detect_window.set_image_available(bool(self._images))
```

`closeEvent()` にウィンドウを閉じる処理を足す:

```python
    def closeEvent(self, event):
        if self._confirm_discard():
            if self._detect_window is not None:
                self._detect_window.close()
            self._worker.stop()
            self._settings.set_geometry(self.saveGeometry())
            event.accept()
        else:
            event.ignore()
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS

Run: `python -m pytest tests/ -v`
Expected: PASS (全件)

- [ ] **Step 5: 手動で動作を確認する**

```bash
python -m mosaic_tool
```

画像をドロップし、`D` キーで自動検出ウィンドウが開くこと、未構築なら `[セットアップ]` が出ること、セットアップ後に標準モデル 3 件が一覧に現れること、`[検出実行]` でプログレスが進み範囲が非選択で追加されることを確認する。

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/app.py tests/test_app.py
git commit -m "feat(app): 自動検出ウィンドウをツールバーから開くようにする"
```

---

### Task 11: ドキュメントの更新

**Files:**
- Modify: `README.md`
- Modify: `docs/detection-models.md`

**Interfaces:**
- Consumes: Task 1〜10 で確定した挙動
- Produces: なし(ドキュメントのみ)

- [ ] **Step 1: README の「自動検出」節を書き換える**

`README.md` の `## 自動検出 (任意)` 節を次に置き換える:

```markdown
## 自動検出 (任意)

YOLO 形式の検出モデルで、モザイク範囲を自動で追加できます。

1. ツールバーの「自動検出」(または `D` キー) を押すと自動検出ウィンドウが開きます
2. 初回は「セットアップ」を押します。推論用の実行環境 (CPU 版 約 250MB / GPU 版 約 2.5GB) と、
   標準の検出モデル 3 件 (顔・目・髪 / 合計 約 20MB) がダウンロードされます。
   `MosaicTool.exe` と同じ場所の `runtime` / `models` フォルダに入ります
3. 使うモデルにチェックを入れ、モデルごとに信頼度 (%) を決めて「検出実行」を押します

信頼度を下げると検出されやすくなります。適切な値はモデルによって違うため、
一覧の初期値は検証済みの推奨値です (根拠は [docs/detection-models.md](https://github.com/kidonaru/MosaicTool/blob/main/docs/detection-models.md))。

検出された範囲は通常の範囲と同じように移動・変形・削除でき、`Ctrl+Z` 一回でまとめて取り消せます。
既に引いてある範囲は消えません。

### 標準モデル

[Anzhc/Anzhcs_YOLOs](https://huggingface.co/Anzhc/Anzhcs_YOLOs) (AGPL-3.0) のセグメンテーションモデルです。

| モデル | 対象 | サイズ |
|---|---|---|
| `Anzhc Face seg 640 v4 y11n.pt` | 顔 | 5.7MB |
| `Anzhc Eyes -seg-hd.pt` | 目 | 6.6MB |
| `Anzhc HeadHair seg y8n.pt` | 髪 | 6.5MB |

### その他の検出モデル

以下は自動ダウンロードの対象外です。手動でダウンロードして `models` フォルダへ置き、
自動検出ウィンドウの「更新」を押すと一覧に現れます。

| モデル | 対象 | 入手先 | 推奨信頼度 |
|---|---|---|---|
| `cockAndBallDetection2D_v20.pt` | penis (2D) | [Civitai 310687](https://civitai.com/models/310687) (要ログイン) | 20% |
| `nsfw-seg-vagina-s.pt` | vagina | [NSFW-API/NSFW_Segmentation](https://huggingface.co/NSFW-API/NSFW_Segmentation) | 15% |
| `Anzhc Breasts Seg v1 1024s.pt` | 胸 | [Anzhc/Anzhcs_YOLOs](https://huggingface.co/Anzhc/Anzhcs_YOLOs) | 25% |

`task=segment` のモデルは多角形の範囲になり、`task=detect` のモデルは矩形になります。
モデルのライセンスはそれぞれの配布元に従います (MosaicTool 本体は MIT ですが、
モデルと推論環境は同梱せず実行時に取得するため本体には影響しません)。

バージョンを更新するときは、旧フォルダの `runtime` と `models` を新しい展開先へコピーすると、
実行環境とモデルを再ダウンロードせずに済みます。
```

- [ ] **Step 2: README の「操作」表と「設定の保存」節を更新する**

「操作」表の自動検出の行を書き換える:

```markdown
| 自動検出 | 「自動検出」ボタン / D キー(自動検出ウィンドウが開く) |
```

ショートカット表の `D` の行を書き換える:

```markdown
| D | 自動検出ウィンドウを開く |
```

「設定の保存」の「変更の都度、即時保存されるもの」に 1 行足す:

```markdown
- 検出モデルごとの ON/OFF と信頼度
```

- [ ] **Step 3: 検証レポートの「今後の検討事項」を更新する**

`docs/detection-models.md` の末尾の節を次に置き換える:

```markdown
## 今後の検討事項

- **検出元モデルの表示**: 現在 `Region` はどのモデル由来かを持たないため、UI から判別できない。
  不要なモデルを外したいときの手がかりが無い(モデル単位の ON/OFF は自動検出ウィンドウで行える)
- **髪モデルの信頼度**: `Anzhc HeadHair seg y8n.pt` は標準モデルに加えたが、
  この検証には含めていない。推奨値は暫定で 25%
```

「モデルごとの信頼度設定」の項目は実装済みのため削除する。

- [ ] **Step 4: リンク切れとテーブルの体裁を目視で確認する**

```bash
grep -n "huggingface.co\|civitai.com" README.md docs/detection-models.md
```

`Anzhc HeadHair seg y8n.pt` が README の標準モデル表と `catalog.py` の `MODELS` で一致していることを確認する:

```bash
grep -n "HeadHair" README.md mosaic_tool/detect/catalog.py
```

- [ ] **Step 5: コミット**

```bash
git add README.md docs/detection-models.md
git commit -m "docs: 自動検出ウィンドウと標準モデルの手順を反映"
```

---

## 完了条件

- [ ] `python -m pytest` が全件パスする
- [ ] `python -m mosaic_tool` で画像を開き、`D` キーで自動検出ウィンドウが開く
- [ ] 未セットアップ環境で「セットアップ」→ CPU が既定で選ばれ、venv 構築後に標準モデル 3 件が `models\` に落ちる
- [ ] モデルのチェックを外すとそのモデルが検出に使われない
- [ ] 検出中にプログレスバーがモデル単位で進む
- [ ] 検出後に追加された範囲が非選択状態になっている
- [ ] アプリを再起動してもチェック状態と信頼度が復元される
