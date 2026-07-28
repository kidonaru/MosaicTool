# 自動モザイク範囲検出 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YOLO 系モデルで検出したセンシティブ部位を、MosaicTool の編集可能な範囲(`Region`)として自動追加できるようにする。

**Architecture:** 本体 exe は PySide6 + Pillow のまま維持し、ultralytics / torch は exe と同じ場所の `runtime\` に作る venv 側だけに置く。両者は標準入出力の JSON 1 行 1 メッセージで会話する常駐ワーカー構成とする。検出結果のマスク輪郭は新種別 `RegionKind.POLYGON` として既存の編集機構に載せる。

**Tech Stack:** Python 3.10+ / PySide6 (QGraphicsView, QProcess) / Pillow / pytest / uv (venv 構築) / ultralytics (venv 側のみ)

設計書: `docs/superpowers/specs/2026-07-28-auto-detect-design.md`

## Global Constraints

- 本体パッケージ `mosaic_tool` は `ultralytics` / `torch` を **import しない**。`requirements.txt` にも追加しない(本体の依存は PySide6 / Pillow / pytest のみ)。
- `mosaic_tool/detect/worker_main.py` だけは例外的に ultralytics を使うが、**関数の中で import** し、`mosaic_tool` パッケージを一切 import しない(venv には入っていないため)。
- コードのコメントとエラーメッセージは日本語で書く。
- マジックナンバーはモジュール定数として名前を付けて定義する。
- テストは pytest。Qt を触るテストは `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` を PySide6 の widgets import より前に置き、`qapp` フィクスチャを使う(既存 `tests/test_canvas.py` と同じ形)。
- テスト実行は `python -m pytest` を使う。
- コミットメッセージは Conventional Commits 形式の日本語。
- 信頼度の既定値は 25%(automosaic の `--confidence 0.25` 準拠)。
- venv の Python バージョンは 3.11。
- CUDA 版 torch のインデックス URL は `https://download.pytorch.org/whl/cu121`(automosaic 準拠)。

---

### Task 1: POLYGON 範囲種別

セグメンテーションマスクの輪郭を保持できるよう `Region` に多角形種別を追加する。既存の変形・変換機構(`image_transform` / `image_path`)は `local_path()` の上に乗っているため、分岐を 1 つ足すだけで移動・拡大縮小・回転がそのまま効く。

**Files:**
- Modify: `mosaic_tool/regions.py:11-13` (enum), `mosaic_tool/regions.py:29-48` (`local_path`)
- Test: `tests/test_regions.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `RegionKind.POLYGON`。`Region(kind=RegionKind.POLYGON, points=[QPointF, ...])` で閉じた多角形を表す。`points` は 3 点以上を前提とする。

- [ ] **Step 1: Write the failing test**

`tests/test_regions.py` の末尾に追記する:

```python
def test_polygon_local_path_bounds():
    # 三角形の外接矩形は (0,0,100,50)
    r = Region(
        kind=RegionKind.POLYGON,
        points=[QPointF(0, 0), QPointF(100, 0), QPointF(100, 50)],
    )
    assert r.local_path().boundingRect() == QRectF(0, 0, 100, 50)


def test_polygon_is_closed_shape():
    # 始点と終点をつないだ閉じた図形になり、内部の点を含む
    r = Region(
        kind=RegionKind.POLYGON,
        points=[QPointF(0, 0), QPointF(100, 0), QPointF(100, 50)],
    )
    assert r.local_path().contains(QPointF(80, 20))
    assert not r.local_path().contains(QPointF(20, 40))


def test_polygon_rotation_90_around_center():
    # 矩形と同じく中心回りに回転する(幅と高さが入れ替わり中心は不変)
    r = Region(
        kind=RegionKind.POLYGON,
        points=[QPointF(0, 0), QPointF(100, 0), QPointF(100, 50), QPointF(0, 50)],
        rotation=90,
    )
    br = r.image_path().boundingRect()
    assert abs(br.width() - 50) < 1e-6
    assert abs(br.height() - 100) < 1e-6
    assert abs(br.center().x() - 50) < 1e-6
    assert abs(br.center().y() - 25) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regions.py -v`
Expected: FAIL(`AttributeError: POLYGON` / `RegionKind` に POLYGON が無い)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/regions.py` の import に `QPolygonF` を追加する:

```python
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QPolygonF, QTransform
```

enum に種別を追加する:

```python
class RegionKind(Enum):
    RECT = "rect"
    STROKE = "stroke"
    POLYGON = "polygon"   # points を閉じた多角形として扱う(検出マスクの輪郭)
```

`local_path()` の RECT 分岐の直後に追加する:

```python
        if self.kind is RegionKind.POLYGON:
            # 検出マスクの輪郭。始点と終点をつないだ閉じた図形にする
            path.addPolygon(QPolygonF(self.points))
            path.closeSubpath()
            return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_regions.py -v`
Expected: PASS(既存テストも含めて全件)

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/regions.py tests/test_regions.py
git commit -m "feat(regions): 多角形の範囲種別 POLYGON を追加"
```

---

### Task 2: 検出結果 JSON → Region 変換

ワーカーとの境界を JSON に置いたことで、この層は ultralytics も Qt のイベントループも要らず単体テストできる。検出結果の解釈・輪郭点の間引き・リクエスト組み立てをここに集約する。

**Files:**
- Create: `mosaic_tool/detect/__init__.py`, `mosaic_tool/detect/convert.py`
- Test: `tests/test_detect_convert.py`

**Interfaces:**
- Consumes: `RegionKind.POLYGON`(Task 1)
- Produces:
  - `DetectError(Exception)`
  - `build_request(image_path: str, conf: float, device: str) -> str`(末尾に改行を含む 1 行 JSON)
  - `parse_response(line: str) -> list[dict]`(失敗時 `DetectError`)
  - `detections_to_regions(detections: list[dict], image_size: tuple[int, int]) -> list[Region]`
  - `thin_points(points: list[QPointF], min_distance: float) -> list[QPointF]`

- [ ] **Step 1: Write the failing test**

`tests/test_detect_convert.py` を新規作成する:

```python
"""検出結果 JSON → Region 変換の検証(ultralytics も Qt の画面も要らない層)"""
import json

import pytest
from PySide6.QtCore import QPointF

from mosaic_tool.detect.convert import (
    DetectError,
    build_request,
    detections_to_regions,
    parse_response,
    thin_points,
)
from mosaic_tool.regions import RegionKind

IMAGE_SIZE = (1000, 1000)


def test_build_request_is_one_json_line():
    line = build_request("C:/img.png", 0.25, "cpu")
    assert line.endswith("\n")
    assert json.loads(line) == {"image": "C:/img.png", "conf": 0.25, "device": "cpu"}


def test_parse_response_returns_detections():
    line = json.dumps({"ok": True, "detections": [{"bbox": [0, 0, 10, 10]}]})
    assert parse_response(line) == [{"bbox": [0, 0, 10, 10]}]


def test_parse_response_raises_on_error_response():
    line = json.dumps({"ok": False, "error": "モデルが壊れています"})
    with pytest.raises(DetectError, match="モデルが壊れています"):
        parse_response(line)


def test_parse_response_raises_on_broken_json():
    with pytest.raises(DetectError):
        parse_response("これは JSON ではない")


def test_polygon_detection_becomes_polygon_region():
    detections = [{"bbox": [0, 0, 100, 100], "polygon": [[0, 0], [100, 0], [100, 100]]}]
    regions = detections_to_regions(detections, IMAGE_SIZE)
    assert len(regions) == 1
    assert regions[0].kind is RegionKind.POLYGON
    assert regions[0].points[0] == QPointF(0, 0)


def test_detection_without_polygon_falls_back_to_rect():
    regions = detections_to_regions([{"bbox": [10, 20, 110, 70]}], IMAGE_SIZE)
    assert len(regions) == 1
    assert regions[0].kind is RegionKind.RECT
    assert regions[0].rect.width() == 100
    assert regions[0].rect.height() == 50


def test_polygon_with_too_few_points_falls_back_to_rect():
    detections = [{"bbox": [10, 20, 110, 70], "polygon": [[0, 0], [100, 0]]}]
    regions = detections_to_regions(detections, IMAGE_SIZE)
    assert regions[0].kind is RegionKind.RECT


def test_detection_without_bbox_and_polygon_is_skipped():
    regions = detections_to_regions([{"conf": 0.9}, {"bbox": [0, 0, 10, 10]}], IMAGE_SIZE)
    assert len(regions) == 1


def test_dense_contour_points_are_thinned():
    # 1px 刻みの 200 点の輪郭 → 1000x1000 の画像では大幅に間引かれる
    dense = [[float(x), 0.0] for x in range(200)] + [[199.0, 100.0], [0.0, 100.0]]
    regions = detections_to_regions(
        [{"bbox": [0, 0, 199, 100], "polygon": dense}], IMAGE_SIZE
    )
    assert regions[0].kind is RegionKind.POLYGON
    assert len(regions[0].points) < 50


def test_thin_points_keeps_at_least_three_points():
    # 全点が近すぎて 3 点未満になる場合は間引かずに元の点列を返す
    pts = [QPointF(0, 0), QPointF(1, 0), QPointF(0, 1)]
    assert len(thin_points(pts, min_distance=1000)) == 3


def test_regions_are_untransformed():
    # 点列は画像座標そのまま。位置・回転・倍率は初期値
    regions = detections_to_regions(
        [{"bbox": [0, 0, 100, 100], "polygon": [[0, 0], [100, 0], [100, 100]]}], IMAGE_SIZE
    )
    r = regions[0]
    assert r.pos == QPointF(0, 0)
    assert r.rotation == 0.0
    assert (r.scale_x, r.scale_y) == (1.0, 1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detect_convert.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'mosaic_tool.detect'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/detect/__init__.py` を作成する:

```python
"""自動モザイク範囲検出(別プロセスの venv 上で動く推論との橋渡し)"""
```

`mosaic_tool/detect/convert.py` を作成する:

```python
"""検出結果 JSON と Region の相互変換(GUI・推論ライブラリ非依存)"""
from __future__ import annotations

import json
import math

from PySide6.QtCore import QPointF, QRectF

from mosaic_tool.regions import Region, RegionKind

# 輪郭点の間引き距離。画像の対角長に対する比率で決める
# (画素数に依らず、見た目の粗さが揃うようにするため)
POLYGON_SIMPLIFY_RATIO = 0.004
# 多角形として成立する最小の点数
MIN_POLYGON_POINTS = 3


class DetectError(Exception):
    """ワーカーからのエラー応答、または応答を解釈できなかったことを表す"""


def build_request(image_path: str, conf: float, device: str) -> str:
    """ワーカーへ送るリクエスト 1 行(改行付き)を組み立てる"""
    payload = {"image": image_path, "conf": conf, "device": device}
    return json.dumps(payload, ensure_ascii=False) + "\n"


def parse_response(line: str) -> list[dict]:
    """ワーカーの応答 1 行を解釈して検出リストを返す"""
    try:
        payload = json.loads(line)
    except (ValueError, TypeError) as e:
        raise DetectError(f"検出結果を解釈できませんでした: {line[:200]}") from e
    if not isinstance(payload, dict):
        raise DetectError(f"検出結果の形式が不正です: {line[:200]}")
    if not payload.get("ok"):
        raise DetectError(payload.get("error") or "検出に失敗しました")
    return payload.get("detections", [])


def thin_points(points: list[QPointF], min_distance: float) -> list[QPointF]:
    """隣接点の距離が min_distance 未満の点を落とす

    輪郭は数百点で返ることがあり、そのまま持つとハンドル操作のたびの
    パス再構築が重くなるため間引く。3 点未満になる場合は元の点列を返す。
    """
    if not points:
        return []
    kept = [points[0]]
    for pt in points[1:]:
        if math.hypot(pt.x() - kept[-1].x(), pt.y() - kept[-1].y()) >= min_distance:
            kept.append(pt)
    if len(kept) < MIN_POLYGON_POINTS:
        return list(points)
    return kept


def _polygon_region(polygon: list, min_distance: float) -> Region | None:
    """輪郭点列から POLYGON 範囲を作る。点が足りなければ None"""
    points = [QPointF(float(x), float(y)) for x, y in polygon]
    if len(points) < MIN_POLYGON_POINTS:
        return None
    return Region(kind=RegionKind.POLYGON, points=thin_points(points, min_distance))


def _rect_region(bbox: list) -> Region | None:
    """bbox から RECT 範囲を作る。値が足りなければ None"""
    if not bbox or len(bbox) < 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    return Region(kind=RegionKind.RECT, rect=QRectF(x1, y1, x2 - x1, y2 - y1))


def detections_to_regions(
    detections: list[dict], image_size: tuple[int, int]
) -> list[Region]:
    """検出結果を範囲へ変換する

    セグメンテーションの輪郭があれば多角形、無ければ bbox の矩形にする。
    どちらも取れない検出は読み飛ばす。
    """
    width, height = image_size
    min_distance = math.hypot(width, height) * POLYGON_SIMPLIFY_RATIO
    regions: list[Region] = []
    for det in detections:
        region = _polygon_region(det.get("polygon") or [], min_distance)
        if region is None:
            region = _rect_region(det.get("bbox") or [])
        if region is not None:
            regions.append(region)
    return regions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detect_convert.py -v`
Expected: PASS(12 件)

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/detect/__init__.py mosaic_tool/detect/convert.py tests/test_detect_convert.py
git commit -m "feat(detect): 検出結果 JSON と Region の変換を追加"
```

---

### Task 3: 範囲の一括追加と 1 回の Undo

検出結果は複数の範囲を一度に追加する。`Ctrl+Z` 一回でまとめて取り消せないと使い勝手が悪いため、Undo スタックに 1 エントリだけ積む経路を用意する。

**Files:**
- Modify: `mosaic_tool/canvas.py:593-606` (`add_region` の直後に追加), `mosaic_tool/canvas.py:619-631` (`undo`)
- Test: `tests/test_canvas.py`

**Interfaces:**
- Consumes: `Region`(Task 1)
- Produces: `MosaicCanvas.add_regions(regions: list[Region]) -> list[RegionItem]`。追加した範囲だけが選択状態になる。Undo スタックには `("add_many", items)` が 1 件積まれる。

- [ ] **Step 1: Write the failing test**

`tests/test_canvas.py` の末尾に追記する:

```python
def _canvas_with_image(qapp) -> MosaicCanvas:
    canvas = MosaicCanvas()
    canvas.set_image(Image.new("RGB", (200, 200), (0, 0, 0)))
    return canvas


def _rect_region(x: float) -> Region:
    return Region(kind=RegionKind.RECT, rect=QRectF(x, 0, 10, 10))


def test_add_regions_adds_all(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_regions([_rect_region(0), _rect_region(20)])
    assert len(canvas.get_regions()) == 2


def test_add_regions_undo_removes_all_at_once(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_regions([_rect_region(0), _rect_region(20)])
    canvas.undo()
    assert canvas.get_regions() == []


def test_add_regions_keeps_existing_regions(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_region(_rect_region(100))
    canvas.add_regions([_rect_region(0), _rect_region(20)])
    canvas.undo()
    # 手で引いた範囲は残る
    assert len(canvas.get_regions()) == 1


def test_add_regions_selects_only_added_regions(qapp):
    canvas = _canvas_with_image(qapp)
    old = canvas.add_region(_rect_region(100))
    old.setSelected(True)
    items = canvas.add_regions([_rect_region(0)])
    assert items[0].isSelected()
    assert not old.isSelected()


def test_add_regions_with_empty_list_pushes_no_undo(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_region(_rect_region(100))
    canvas.add_regions([])
    canvas.undo()
    # 空追加は Undo を消費しないため、直前の追加が取り消される
    assert canvas.get_regions() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canvas.py -v`
Expected: FAIL(`AttributeError: 'MosaicCanvas' object has no attribute 'add_regions'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/canvas.py` の `add_region` の直後に追加する:

```python
    def add_regions(self, regions: list[Region]) -> list[RegionItem]:
        """複数の範囲をまとめて追加する(自動検出用)

        Undo スタックには 1 エントリだけ積み、Ctrl+Z 一回で追加分をまとめて
        取り消せるようにする。追加分だけを選択状態にして、どれが増えたか分かるようにする。
        """
        if not regions:
            return []
        self._scene.clearSelection()
        items = [self.add_region(region, push_undo=False) for region in regions]
        for item in items:
            item.setSelected(True)
        self._undo_stack.append(("add_many", items))
        return items
```

`undo()` の `"add"` 分岐の直後に追加する:

```python
        elif entry[0] == "add_many":
            for item in entry[1]:
                self._scene.removeItem(item)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canvas.py -v`
Expected: PASS(既存テストも含めて全件)

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/canvas.py tests/test_canvas.py
git commit -m "feat(canvas): 範囲の一括追加と 1 回でまとめて戻せる Undo を追加"
```

---

### Task 4: exe 隣を基準にしたパス解決

`models\` と `runtime\` は exe と同じ場所に置く。frozen(PyInstaller)かソース実行かで基準が変わるため、この判定を 1 箇所に閉じ込める。同梱リソース(`uv.exe`)は展開先を見るため基準が別になる点に注意する。

**Files:**
- Create: `mosaic_tool/detect/paths.py`
- Test: `tests/test_detect_paths.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `base_dir() -> Path` / `models_dir() -> Path` / `runtime_dir() -> Path`
  - `model_files() -> list[Path]`(`models\*.pt` をファイル名順)
  - `venv_python() -> Path` / `is_runtime_ready() -> bool`
  - `bundled_uv_path() -> Path` / `worker_script_source() -> Path` / `worker_script_installed() -> Path`

- [ ] **Step 1: Write the failing test**

`tests/test_detect_paths.py` を新規作成する:

```python
"""exe 隣を基準にしたパス解決の検証"""
import sys
from pathlib import Path

from mosaic_tool.detect import paths


def test_base_dir_is_repo_root_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    # ソース実行時は mosaic_tool パッケージの 1 つ上(リポジトリ直下)
    assert (paths.base_dir() / "mosaic_tool").is_dir()


def test_base_dir_is_exe_dir_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MosaicTool.exe"))
    assert paths.base_dir() == tmp_path


def test_models_and_runtime_are_next_to_base(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert paths.models_dir() == tmp_path / "models"
    assert paths.runtime_dir() == tmp_path / "runtime"


def test_model_files_lists_pt_files_sorted(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "b.pt").write_bytes(b"")
    (models / "a.pt").write_bytes(b"")
    (models / "readme.txt").write_text("メモ", encoding="utf-8")
    assert [p.name for p in paths.model_files()] == ["a.pt", "b.pt"]


def test_model_files_is_empty_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert paths.model_files() == []


def test_runtime_is_not_ready_without_venv_python(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert not paths.is_runtime_ready()


def test_runtime_is_ready_when_venv_python_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    scripts = tmp_path / "runtime" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")
    assert paths.is_runtime_ready()


def test_worker_script_source_exists_in_package():
    # ワーカー本体はパッケージに同梱されている
    assert paths.worker_script_source().name == "worker_main.py"


def test_worker_script_is_installed_into_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert paths.worker_script_installed().parent == tmp_path / "runtime"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detect_paths.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'mosaic_tool.detect.paths'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/detect/paths.py` を作成する:

```python
"""自動検出まわりのパス解決

models/ と runtime/ は exe と同じ場所に置く(展開したフォルダごと持ち運べるように)。
同梱リソース(uv.exe, worker_main.py)は onefile では展開先の一時ディレクトリに
現れるため、基準が別になる。
"""
from __future__ import annotations

import sys
from pathlib import Path

MODELS_DIR_NAME = "models"
RUNTIME_DIR_NAME = "runtime"
MODEL_SUFFIX = ".pt"
# runtime\ へコピーするワーカーのファイル名(venv の Python へ渡すため実体が要る)
WORKER_SCRIPT_NAME = "detect_worker.py"
UV_EXE_NAME = "uv.exe"


def base_dir() -> Path:
    """models/ runtime/ を置く基準ディレクトリ

    frozen(PyInstaller)では exe と同じ場所、ソース実行ではリポジトリ直下。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _bundle_dir() -> Path:
    """同梱リソースの基準(onefile では展開先の一時ディレクトリ)"""
    return Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    return base_dir() / MODELS_DIR_NAME


def runtime_dir() -> Path:
    return base_dir() / RUNTIME_DIR_NAME


def model_files() -> list[Path]:
    """models/ に置かれた検出モデルをファイル名順に返す"""
    directory = models_dir()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(f"*{MODEL_SUFFIX}") if p.is_file())


def venv_python() -> Path:
    return runtime_dir() / "Scripts" / "python.exe"


def is_runtime_ready() -> bool:
    """推論環境(venv)が構築済みか"""
    return venv_python().is_file()


def bundled_uv_path() -> Path:
    return _bundle_dir() / UV_EXE_NAME


def worker_script_source() -> Path:
    return Path(__file__).resolve().parent / "worker_main.py"


def worker_script_installed() -> Path:
    return runtime_dir() / WORKER_SCRIPT_NAME
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detect_paths.py -v`
Expected: `test_worker_script_source_exists_in_package` 以外 PASS。ワーカー本体は Task 6 で作るため、この 1 件は Task 6 完了までスキップとして残さず、次の Step で空ファイルを置いて通す。

- [ ] **Step 5: ワーカーのプレースホルダを置いてテストを通す**

`mosaic_tool/detect/worker_main.py` を作成する(中身は Task 6 で実装する):

```python
"""検出ワーカー(venv 側の Python で動く)。実装は Task 6 で行う"""
```

Run: `python -m pytest tests/test_detect_paths.py -v`
Expected: PASS(9 件)

- [ ] **Step 6: Commit**

```bash
git add mosaic_tool/detect/paths.py mosaic_tool/detect/worker_main.py tests/test_detect_paths.py
git commit -m "feat(detect): models/runtime のパス解決を追加"
```

---

### Task 5: uv による推論環境のセットアップ

`uv` を叩いて `runtime\` に venv を作り、ultralytics / torch を入れる。GPU/CPU の分岐はインデックス URL の有無だけなので、コマンド組み立てを純粋関数に切り出してテストする。実行そのものは `QProcess` で非同期に行い、UI を止めない。

**Files:**
- Create: `mosaic_tool/detect/runtime.py`
- Test: `tests/test_detect_runtime.py`

**Interfaces:**
- Consumes: `paths.bundled_uv_path()`, `paths.runtime_dir()`(Task 4)
- Produces:
  - `venv_command(uv: Path, runtime: Path) -> list[str]`
  - `install_command(uv: Path, runtime: Path, use_gpu: bool) -> list[str]`
  - `has_nvidia_gpu() -> bool`
  - `RuntimeInstaller(QObject)`: `progress = Signal(str)` / `finished = Signal(bool, str)` / `start(use_gpu: bool)` / `cancel()`

- [ ] **Step 1: Write the failing test**

`tests/test_detect_runtime.py` を新規作成する:

```python
"""推論環境セットアップのコマンド組み立ての検証(uv は実行しない)"""
from pathlib import Path

from mosaic_tool.detect import runtime

UV = Path("C:/app/uv.exe")
RUNTIME = Path("C:/app/runtime")


def test_venv_command_pins_python_version():
    cmd = runtime.venv_command(UV, RUNTIME)
    assert cmd[:3] == [str(UV), "venv", str(RUNTIME)]
    assert cmd[-2:] == ["--python", runtime.PYTHON_VERSION]


def test_install_command_targets_the_venv():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=False)
    assert cmd[:3] == [str(UV), "pip", "install"]
    assert "--python" in cmd and str(RUNTIME) in cmd
    for package in runtime.PACKAGES:
        assert package in cmd


def test_cpu_install_has_no_cuda_index():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=False)
    assert "--extra-index-url" not in cmd


def test_gpu_install_adds_cuda_index():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=True)
    assert cmd[-2:] == ["--extra-index-url", runtime.TORCH_CUDA_INDEX_URL]


def test_has_nvidia_gpu_uses_nvidia_smi(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "C:/w/nvidia-smi.exe")
    assert runtime.has_nvidia_gpu()
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    assert not runtime.has_nvidia_gpu()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detect_runtime.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'mosaic_tool.detect.runtime'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/detect/runtime.py` を作成する:

```python
"""推論環境(venv)のセットアップ: uv で Python と ultralytics/torch を用意する"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from mosaic_tool.detect import paths

# venv に入れる Python のバージョン(ultralytics/torch の対応が安定している系列)
PYTHON_VERSION = "3.11"
PACKAGES = ["ultralytics", "torch", "torchvision"]
# CUDA 版 torch の配布元(automosaic と同じ cu121 系)
TORCH_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu121"


def venv_command(uv: Path, runtime: Path) -> list[str]:
    """runtime/ に venv を作るコマンド"""
    return [str(uv), "venv", str(runtime), "--python", PYTHON_VERSION]


def install_command(uv: Path, runtime: Path, use_gpu: bool) -> list[str]:
    """runtime/ の venv へ推論パッケージを入れるコマンド

    GPU 版は torch の配布元が PyPI ではないため、追加のインデックスを指定する。
    """
    cmd = [str(uv), "pip", "install", "--python", str(runtime), *PACKAGES]
    if use_gpu:
        cmd += ["--extra-index-url", TORCH_CUDA_INDEX_URL]
    return cmd


def has_nvidia_gpu() -> bool:
    """NVIDIA GPU がありそうか(セットアップ時の既定値の出し分けに使う)"""
    return shutil.which("nvidia-smi") is not None


class RuntimeInstaller(QObject):
    """venv 作成 → パッケージ導入 を順に実行する(非同期)"""

    progress = Signal(str)          # 進捗ログ 1 行
    finished = Signal(bool, str)    # (成功したか, メッセージ)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._steps: list[list[str]] = []
        self._cancelled = False

    def start(self, use_gpu: bool) -> None:
        uv = paths.bundled_uv_path()
        if not uv.is_file():
            self.finished.emit(False, f"uv が見つかりません: {uv}")
            return
        runtime_dir = paths.runtime_dir()
        self._cancelled = False
        self._steps = [
            venv_command(uv, runtime_dir),
            install_command(uv, runtime_dir, use_gpu),
        ]
        self._run_next()

    def cancel(self) -> None:
        self._cancelled = True
        self._steps = []
        if self._process is not None:
            self._process.kill()

    def _run_next(self) -> None:
        if not self._steps:
            self.finished.emit(True, "推論環境のセットアップが完了しました")
            return
        cmd = self._steps.pop(0)
        self.progress.emit(f"> {' '.join(cmd)}")
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_step_finished)
        self._process = process
        process.start(cmd[0], cmd[1:])

    def _on_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        for line in text.splitlines():
            if line.strip():
                self.progress.emit(line)

    def _on_step_finished(self, exit_code: int, _status) -> None:
        self._process = None
        if self._cancelled:
            self._cleanup()
            self.finished.emit(False, "セットアップを中止しました")
            return
        if exit_code != 0:
            self._cleanup()
            self.finished.emit(False, f"セットアップに失敗しました (終了コード {exit_code})")
            return
        self._run_next()

    def _cleanup(self) -> None:
        """中途半端な venv を残さない(次回はやり直しから始められる)"""
        shutil.rmtree(paths.runtime_dir(), ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detect_runtime.py -v`
Expected: PASS(5 件)

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/detect/runtime.py tests/test_detect_runtime.py
git commit -m "feat(detect): uv による推論環境のセットアップを追加"
```

---

### Task 6: 検出ワーカー(venv 側で動くスクリプト)

venv の Python から実行される唯一のスクリプト。`mosaic_tool` は venv に入っていないため **import してはならない**。ultralytics も関数の中で import することで、本体側のテストからこのモジュールを読み込んで検証できるようにする。

**Files:**
- Modify: `mosaic_tool/detect/worker_main.py`(Task 4 で置いたプレースホルダを実装で置き換える)
- Test: `tests/test_detect_worker_main.py`

**Interfaces:**
- Consumes: なし(完全に自己完結)
- Produces:
  - コマンドライン: `python detect_worker.py <model1.pt> <model2.pt> ...`
  - 起動直後に `{"ok": true, "ready": true}` を 1 行出力する
  - 以降、stdin の 1 行 `{"image":..., "conf":..., "device":...}` ごとに `{"ok": true, "detections":[...]}` を 1 行返す
  - 検出 1 件は `{"model": str, "conf": float, "bbox": [x1,y1,x2,y2], "polygon": [[x,y],...](任意)}`
  - `detect(models, image_path, conf, device) -> list[dict]`

- [ ] **Step 1: Write the failing test**

`tests/test_detect_worker_main.py` を新規作成する:

```python
"""検出ワーカーの検証(ultralytics は入れず、偽のモデルで振る舞いだけ確かめる)"""
from types import SimpleNamespace

from mosaic_tool.detect import worker_main
from mosaic_tool.detect.paths import worker_script_source


class FakeBox:
    def __init__(self, conf, xyxy):
        self.conf = [conf]
        self.xyxy = [SimpleNamespace(tolist=lambda v=xyxy: list(v))]


class FakeResult:
    def __init__(self, boxes, polygons=None):
        self.boxes = boxes
        self.masks = SimpleNamespace(xy=polygons) if polygons is not None else None


class FakeModel:
    """呼ばれたら固定の検出結果を返すモデル"""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def __call__(self, image, conf=None, device=None, verbose=None):
        self.calls.append({"image": image, "conf": conf, "device": device})
        return [self._result]


def test_worker_does_not_import_mosaic_tool():
    # venv 側には mosaic_tool が無いため、依存してはならない
    source = worker_script_source().read_text(encoding="utf-8")
    assert "mosaic_tool" not in source


def test_detect_returns_bbox_and_model_name():
    model = FakeModel(FakeResult([FakeBox(0.9, [1, 2, 3, 4])]))
    result = worker_main.detect([("pussyV2.pt", model)], "img.png", 0.25, "")
    assert result == [
        {"model": "pussyV2.pt", "conf": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]}
    ]


def test_detect_includes_polygon_when_masks_exist():
    model = FakeModel(
        FakeResult([FakeBox(0.9, [0, 0, 10, 10])], polygons=[[(0, 0), (10, 0), (10, 10)]])
    )
    result = worker_main.detect([("m.pt", model)], "img.png", 0.25, "")
    assert result[0]["polygon"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]


def test_detect_combines_multiple_models():
    a = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    b = FakeModel(FakeResult([FakeBox(0.8, [2, 2, 3, 3])]))
    result = worker_main.detect([("a.pt", a), ("b.pt", b)], "img.png", 0.3, "cpu")
    assert [d["model"] for d in result] == ["a.pt", "b.pt"]


def test_detect_passes_conf_and_device_to_model():
    model = FakeModel(FakeResult([]))
    worker_main.detect([("m.pt", model)], "img.png", 0.4, "cpu")
    assert model.calls[0]["conf"] == 0.4
    assert model.calls[0]["device"] == "cpu"


def test_empty_device_is_passed_as_none():
    # 空文字は ultralytics へ渡さず自動選択に任せる
    model = FakeModel(FakeResult([]))
    worker_main.detect([("m.pt", model)], "img.png", 0.4, "")
    assert model.calls[0]["device"] is None


def test_handle_request_returns_error_payload_on_failure():
    class BrokenModel:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("推論に失敗")

    payload = worker_main.handle_request(
        [("m.pt", BrokenModel())], '{"image": "img.png", "conf": 0.25}'
    )
    assert payload["ok"] is False
    assert "推論に失敗" in payload["error"]


def test_handle_request_returns_detections():
    model = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    payload = worker_main.handle_request(
        [("m.pt", model)], '{"image": "img.png", "conf": 0.25, "device": ""}'
    )
    assert payload["ok"] is True
    assert len(payload["detections"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detect_worker_main.py -v`
Expected: FAIL(`AttributeError: module 'mosaic_tool.detect.worker_main' has no attribute 'detect'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/detect/worker_main.py` の内容をすべて次で置き換える:

```python
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

DEFAULT_CONFIDENCE = 0.25


def load_models(model_paths: list[str]) -> list[tuple]:
    """モデルを読み込む((表示名, モデル) の列を返す)"""
    from ultralytics import YOLO

    return [(Path(p).name, YOLO(p)) for p in model_paths]


def detect(models: list[tuple], image_path: str, conf: float, device: str) -> list[dict]:
    """全モデルで推論し、検出を 1 つの列にまとめて返す"""
    detections: list[dict] = []
    for name, model in models:
        # device が空なら ultralytics の自動選択に任せる
        result = model(image_path, conf=conf, device=device or None, verbose=False)[0]
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
    return detections


def handle_request(models: list[tuple], line: str) -> dict:
    """リクエスト 1 行を処理して応答の中身を返す(失敗しても例外を外へ出さない)"""
    try:
        request = json.loads(line)
        detections = detect(
            models,
            request["image"],
            float(request.get("conf", DEFAULT_CONFIDENCE)),
            request.get("device", ""),
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
            _emit(handle_request(models, line))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detect_worker_main.py tests/test_detect_paths.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/detect/worker_main.py tests/test_detect_worker_main.py
git commit -m "feat(detect): venv 側で動く検出ワーカーを追加"
```

---

### Task 7: 常駐ワーカーのクライアント

ワーカーを起動して常駐させ、リクエストを送って結果を受け取る。モデル読み込みに数秒かかるため、プロセスは使い回す。応答の切り出し(バッファリング)を `_feed()` に分離し、`QProcess` を動かさずにプロトコルを検証できるようにする。

**Files:**
- Create: `mosaic_tool/detect/worker_client.py`
- Test: `tests/test_detect_worker_client.py`

**Interfaces:**
- Consumes: `convert.build_request` / `convert.parse_response` / `convert.DetectError`(Task 2)、`paths.*`(Task 4)
- Produces:
  - `worker_command(python: Path, script: Path, models: list[Path]) -> list[str]`
  - `install_worker_script() -> Path`(`worker_main.py` を `runtime\detect_worker.py` へコピー)
  - `DetectWorker(QObject)`: `detected = Signal(list)` / `failed = Signal(str)` / `request(image_path, conf, device)` / `stop()` / `is_busy() -> bool`

- [ ] **Step 1: Write the failing test**

`tests/test_detect_worker_client.py` を新規作成する:

```python
"""ワーカークライアントのコマンド組み立て・スクリプト設置・応答の切り出しの検証"""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect import paths, worker_client  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_worker_command_lists_models_as_arguments():
    python = Path("C:/rt/python.exe")
    script = Path("C:/rt/detect_worker.py")
    models = [Path("C:/m/a.pt"), Path("C:/m/b.pt")]
    cmd = worker_client.worker_command(python, script, models)
    assert cmd == [str(python), str(script), str(models[0]), str(models[1])]


def test_install_worker_script_copies_source(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "runtime").mkdir()
    installed = worker_client.install_worker_script()
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == paths.worker_script_source().read_text(
        encoding="utf-8"
    )


def test_install_worker_script_overwrites_stale_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "runtime").mkdir()
    stale = paths.worker_script_installed()
    stale.write_text("古い内容", encoding="utf-8")
    worker_client.install_worker_script()
    assert stale.read_text(encoding="utf-8") != "古い内容"


def _worker(qapp) -> worker_client.DetectWorker:
    return worker_client.DetectWorker()


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detect_worker_client.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'mosaic_tool.detect.worker_client'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/detect/worker_client.py` を作成する:

```python
"""常駐する検出ワーカーの制御(起動・リクエスト・応答の切り出し)"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from mosaic_tool.detect import paths
from mosaic_tool.detect.convert import DetectError, build_request, parse_response

# 1 枚あたりの検出を待つ上限(モデル読み込みを含む初回を見込んで長めに取る)
DETECT_TIMEOUT_MS = 120_000
# 異常終了時に表示する stderr の末尾の文字数
STDERR_TAIL = 500


def worker_command(python: Path, script: Path, models: list[Path]) -> list[str]:
    """ワーカーの起動コマンド(モデルは引数として並べて渡す)"""
    return [str(python), str(script), *(str(m) for m in models)]


def install_worker_script() -> Path:
    """ワーカー本体を runtime/ へコピーする

    venv の Python はパッケージ内のモジュールを解決できないため、
    実体のスクリプトファイルとして置く必要がある。内容は毎回上書きし、
    アプリを更新したときに古いワーカーが残らないようにする。
    """
    destination = paths.worker_script_installed()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(paths.worker_script_source(), destination)
    return destination


class DetectWorker(QObject):
    """検出ワーカーとの通信を受け持つ

    モデル読み込みに数秒かかるためプロセスは常駐させ、異常終了しても
    次のリクエストで黙って起動し直す。
    """

    detected = Signal(list)   # 検出結果 (list[dict])
    failed = Signal(str)      # エラーメッセージ

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._buffer = ""
        self._ready = False
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DETECT_TIMEOUT_MS)
        self._timer.timeout.connect(self._on_timeout)

    def is_busy(self) -> bool:
        return self._busy

    def request(self, image_path: str, conf: float, device: str) -> None:
        """検出を依頼する(結果は detected / failed で返る)"""
        if self._busy:
            return
        models = paths.model_files()
        if not models:
            self.failed.emit(
                f"検出モデルが見つかりません: {paths.models_dir()}"
            )
            return
        if self._process is None and not self._start(models):
            return
        self._busy = True
        self._timer.start()
        self._process.write(build_request(image_path, conf, device).encode("utf-8"))

    def stop(self) -> None:
        """ワーカーを終了する(アプリ終了時に呼ぶ)"""
        self._timer.stop()
        process, self._process = self._process, None
        self._busy = False
        self._ready = False
        self._buffer = ""
        if process is None:
            return
        process.terminate()
        if not process.waitForFinished(3000):
            process.kill()

    def _start(self, models: list[Path]) -> bool:
        try:
            script = install_worker_script()
        except OSError as e:
            self.failed.emit(f"ワーカーの設置に失敗しました: {e}")
            return False
        cmd = worker_command(paths.venv_python(), script, models)
        process = QProcess(self)
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.finished.connect(self._on_process_finished)
        process.errorOccurred.connect(self._on_process_error)
        process.start(cmd[0], cmd[1:])
        if not process.waitForStarted(10_000):
            self.failed.emit(f"検出ワーカーを起動できませんでした: {cmd[0]}")
            return False
        self._process = process
        self._buffer = ""
        self._ready = False
        return True

    def _on_stdout(self) -> None:
        if self._process is None:
            return
        self._feed(
            bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        )

    def _feed(self, chunk: str) -> None:
        """標準出力の断片を受け取り、行が揃うたびに 1 応答として処理する"""
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        try:
            detections = parse_response(line)
        except DetectError as e:
            self._finish_request()
            self.failed.emit(str(e))
            return
        if not self._ready:
            # 起動直後の 1 行はモデル読み込み完了の通知
            self._ready = True
            return
        self._finish_request()
        self.detected.emit(detections)

    def _finish_request(self) -> None:
        self._busy = False
        self._timer.stop()

    def _on_timeout(self) -> None:
        self._finish_request()
        self.stop()
        self.failed.emit("検出がタイムアウトしました")

    def _on_process_finished(self, exit_code: int, _status) -> None:
        # 応答待ちのまま終了したときだけエラーとして扱う(次回は起動し直す)
        was_busy = self._busy
        detail = ""
        if self._process is not None:
            detail = bytes(self._process.readAllStandardError()).decode(
                "utf-8", errors="replace"
            )[-STDERR_TAIL:]
        self._process = None
        self._ready = False
        self._finish_request()
        if was_busy:
            self.failed.emit(
                f"検出ワーカーが終了しました (終了コード {exit_code})\n{detail}".strip()
            )

    def _on_process_error(self, _error) -> None:
        if self._busy:
            self._finish_request()
            self.failed.emit("検出ワーカーとの通信に失敗しました")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detect_worker_client.py -v`
Expected: PASS(8 件)

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/detect/worker_client.py tests/test_detect_worker_client.py
git commit -m "feat(detect): 常駐ワーカーのクライアントを追加"
```

---

### Task 8: 信頼度とデバイスの設定を永続化

自動検出のパラメータを次回起動時に復元できるようにする。既存の `AppSettings` の書き方に合わせる。

**Files:**
- Modify: `mosaic_tool/settings.py:14-26`(既定値とキー), `mosaic_tool/settings.py:84-91` の直後(アクセサ)
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `AppSettings.confidence(minimum: int, maximum: int) -> int` / `set_confidence(value: int)`
  - `AppSettings.device() -> str`("auto" または "cpu") / `set_device(value: str)`
  - 定数 `DEFAULT_CONFIDENCE = 25` / `DEFAULT_DEVICE = "auto"`

- [ ] **Step 1: Write the failing test**

`tests/test_settings.py` の末尾に追記する:

```python
def test_confidence_defaults_to_25(tmp_path):
    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    assert settings.confidence(1, 100) == 25


def test_confidence_roundtrip(tmp_path):
    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    settings.set_confidence(40)
    assert settings.confidence(1, 100) == 40


def test_confidence_out_of_range_falls_back_to_default(tmp_path):
    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    settings.set_confidence(500)
    assert settings.confidence(1, 100) == 25


def test_device_defaults_to_auto(tmp_path):
    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    assert settings.device() == "auto"


def test_device_roundtrip_and_invalid_value(tmp_path):
    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    settings.set_device("cpu")
    assert settings.device() == "cpu"
    settings.set_device("gpu")
    assert settings.device() == "auto"
```

ファイル先頭の import に `QSettings` と `AppSettings` が無ければ追加する:

```python
from PySide6.QtCore import QSettings

from mosaic_tool.settings import AppSettings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL(`AttributeError: 'AppSettings' object has no attribute 'confidence'`)

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/settings.py` の既定値ブロックに追加する:

```python
DEFAULT_CONFIDENCE = 25   # 自動検出の信頼度しきい値 (%)
DEFAULT_DEVICE = "auto"   # 推論デバイス ("auto" / "cpu")
```

キーの定義に追加する:

```python
_KEY_CONFIDENCE = "detect/confidence"
_KEY_DEVICE = "detect/device"
```

ツールモードのアクセサの直後に追加する:

```python
    # --- 自動検出 ---

    def confidence(self, minimum: int, maximum: int) -> int:
        return self._int(_KEY_CONFIDENCE, DEFAULT_CONFIDENCE, minimum, maximum)

    def set_confidence(self, value: int) -> None:
        self._qsettings.setValue(_KEY_CONFIDENCE, int(value))

    def device(self) -> str:
        value = str(self._qsettings.value(_KEY_DEVICE, DEFAULT_DEVICE))
        return value if value in ("auto", "cpu") else DEFAULT_DEVICE

    def set_device(self, value: str) -> None:
        self._qsettings.setValue(_KEY_DEVICE, value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS(既存テストも含めて全件)

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/settings.py tests/test_settings.py
git commit -m "feat(settings): 自動検出の信頼度とデバイス設定を追加"
```

---

### Task 9: メインウィンドウへの統合

ツールバーに操作を追加し、セットアップ導線・検出実行・結果の反映をつなぐ。すべて非同期なので UI は固まらない。

**Files:**
- Create: `mosaic_tool/detect/setup_dialog.py`
- Modify: `mosaic_tool/app.py`(import、`__init__`、`_build_toolbar` の末尾、ハンドラ群、`closeEvent`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `DetectWorker`(Task 7)、`RuntimeInstaller` / `has_nvidia_gpu`(Task 5)、`detections_to_regions`(Task 2)、`paths.is_runtime_ready` / `paths.model_files` / `paths.models_dir`(Task 4)、`canvas.add_regions`(Task 3)、`AppSettings.confidence` / `device`(Task 8)
- Produces:
  - `RuntimeSetupDialog(QDialog)`: `exec() -> int`(`QDialog.DialogCode.Accepted` ならセットアップ成功)
  - `MainWindow._detect_act` / `MainWindow._confidence_spin`
  - `MainWindow._on_detect()` / `MainWindow._on_detected(detections: list[dict])` / `MainWindow._on_detect_failed(message: str)`

- [ ] **Step 1: Write the failing test**

`tests/test_app.py` の末尾に追記する:

```python
def test_detect_action_shortcut_is_d(window):
    assert window._detect_act.shortcut() == QKeySequence(Qt.Key.Key_D)


def test_confidence_spin_restores_setting(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    settings.set_confidence(40)
    win = MainWindow(settings=settings)
    try:
        assert win._confidence_spin.value() == 40
    finally:
        win.close()


def test_confidence_change_is_saved(window):
    window._confidence_spin.setValue(55)
    assert window._settings.confidence(1, 100) == 55


def test_detected_regions_are_added_to_canvas(window):
    window._on_detected([{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}])
    assert len(window.canvas.get_regions()) == 2


def test_detected_regions_can_be_undone_at_once(window):
    window._on_detected([{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}])
    window.canvas.undo()
    assert window.canvas.get_regions() == []


def test_empty_detection_shows_message(window):
    window._on_detected([])
    assert "検出されませんでした" in window.statusBar().currentMessage()


def test_detect_failure_shows_error(window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical",
        lambda *args, **kwargs: shown.append(args[2]),
    )
    window._on_detect_failed("モデルの読み込みに失敗しました")
    assert shown and "モデルの読み込み" in shown[0]


def test_detect_without_models_warns_and_does_not_start(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)
    monkeypatch.setattr("mosaic_tool.app.detect_paths.model_files", lambda: [])
    warned = []
    monkeypatch.setattr(window, "_warn_models_missing", lambda: warned.append(True))
    requested = []
    monkeypatch.setattr(window._worker, "request", lambda *a: requested.append(a))
    window._on_detect()
    assert warned and not requested


def test_detect_starts_worker_when_ready(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)
    monkeypatch.setattr(
        "mosaic_tool.app.detect_paths.model_files", lambda: [Path("dummy.pt")]
    )
    requested = []
    monkeypatch.setattr(window._worker, "request", lambda *a: requested.append(a))
    window._on_detect()
    assert len(requested) == 1
    # (画像パス, 信頼度 0.0-1.0, デバイス)
    assert requested[0][1] == window._confidence_spin.value() / 100
```

ファイル先頭の import に `Path` を追加する:

```python
from pathlib import Path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL(`AttributeError: 'MainWindow' object has no attribute '_detect_act'`)

- [ ] **Step 3: セットアップダイアログを作る**

`mosaic_tool/detect/setup_dialog.py` を作成する:

```python
"""推論環境のセットアップ用ダイアログ(GPU/CPU の選択と進捗表示)"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
)

from mosaic_tool.detect.runtime import RuntimeInstaller, has_nvidia_gpu

INTRO = (
    "自動検出を使うには、推論用の実行環境を用意する必要があります。\n"
    "ダウンロードには時間がかかります(回線状況により数分〜十数分)。"
)
GPU_LABEL = "GPU を使う (NVIDIA / ダウンロード 約 2.5GB / 検出が速い)"
CPU_LABEL = "CPU のみ (ダウンロード 約 250MB / どの環境でも動く)"


class RuntimeSetupDialog(QDialog):
    """セットアップの選択と実行。完了すると accept() する"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自動検出のセットアップ")
        self.resize(680, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(INTRO))
        self._gpu_radio = QRadioButton(GPU_LABEL)
        self._cpu_radio = QRadioButton(CPU_LABEL)
        # NVIDIA GPU がありそうなら GPU 版を既定にする
        self._gpu_radio.setChecked(has_nvidia_gpu())
        self._cpu_radio.setChecked(not self._gpu_radio.isChecked())
        layout.addWidget(self._gpu_radio)
        layout.addWidget(self._cpu_radio)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始")
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self._cancel)
        layout.addWidget(self._buttons)

        self._installer = RuntimeInstaller(self)
        self._installer.progress.connect(self._log.appendPlainText)
        self._installer.finished.connect(self._on_finished)
        self._running = False

    def _start(self) -> None:
        self._running = True
        self._set_inputs_enabled(False)
        self._installer.start(use_gpu=self._gpu_radio.isChecked())

    def _cancel(self) -> None:
        if self._running:
            self._installer.cancel()
            return
        self.reject()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._gpu_radio.setEnabled(enabled)
        self._cpu_radio.setEnabled(enabled)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _on_finished(self, ok: bool, message: str) -> None:
        self._running = False
        self._log.appendPlainText(message)
        if ok:
            self.accept()
            return
        self._set_inputs_enabled(True)
```

- [ ] **Step 4: MainWindow へ組み込む**

`mosaic_tool/app.py` の import を追加する(既存行の置き換えを含む):

```python
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSlider,
    QSpinBox,
)

from mosaic_tool.detect import paths as detect_paths
from mosaic_tool.detect.convert import detections_to_regions
from mosaic_tool.detect.setup_dialog import RuntimeSetupDialog
from mosaic_tool.detect.worker_client import DetectWorker
```

定数を追加する(既存の定数群の末尾):

```python
CONFIDENCE_MIN = 1    # 自動検出の信頼度しきい値の下限 (%)
CONFIDENCE_MAX = 100  # 同上限 (%)
CONFIDENCE_STEP = 5   # 矢印ボタンの刻み幅 (%。数値入力は 1% 刻み)
```

`__init__` の `self._build_toolbar()` の直前に追加する:

```python
        # 自動検出(推論は別プロセスの venv 側で動く)
        self._worker = DetectWorker(self)
        self._worker.detected.connect(self._on_detected)
        self._worker.failed.connect(self._on_detect_failed)
```

`_build_toolbar()` の末尾(プレビューの追加後)に追加する:

```python
        tb.addSeparator()
        # 自動検出: 検出した範囲を追加する(既存の範囲は消さない)
        self._detect_act = QAction("自動検出", self)
        self._add_shortcut(self._detect_act, QKeySequence(Qt.Key.Key_D))
        self._detect_act.triggered.connect(self._on_detect)
        tb.addAction(self._detect_act)
        tb.addWidget(QLabel(" 信頼度 "))
        self._confidence_spin = QSpinBox()
        self._confidence_spin.setRange(CONFIDENCE_MIN, CONFIDENCE_MAX)
        self._confidence_spin.setSingleStep(CONFIDENCE_STEP)
        self._confidence_spin.setValue(
            self._settings.confidence(CONFIDENCE_MIN, CONFIDENCE_MAX)
        )
        self._confidence_spin.setSuffix(" %")
        self._confidence_spin.valueChanged.connect(self._settings.set_confidence)
        tb.addWidget(self._confidence_spin)
```

`# --- 未保存確認 ---` の直前に自動検出のハンドラ群を追加する:

```python
    # --- 自動検出 ---

    def _on_detect(self) -> None:
        """表示中の画像に対して自動検出を実行する"""
        if not self._images or self._current_image is None:
            return
        if self._worker.is_busy():
            return
        if not detect_paths.is_runtime_ready():
            dialog = RuntimeSetupDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        if not detect_paths.model_files():
            self._warn_models_missing()
            return
        self._detect_act.setEnabled(False)
        self.statusBar().showMessage("検出中...")
        self._worker.request(
            str(self._images[self._index]),
            self._confidence_spin.value() / 100,
            "" if self._settings.device() == "auto" else "cpu",
        )

    def _warn_models_missing(self) -> None:
        """モデル未配置を案内する(置き場所をすぐ開けるようにする)"""
        directory = detect_paths.models_dir()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("エラー")
        box.setText(
            "検出モデルが見つかりません。\n"
            f"{directory} に .pt ファイルを置いてください。"
        )
        open_button = box.addButton("フォルダを開く", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_button:
            directory.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _on_detected(self, detections: list) -> None:
        """検出結果を範囲として追加する(既存の範囲は残す)"""
        self._detect_act.setEnabled(True)
        if self._current_image is None:
            return
        regions = detections_to_regions(detections, self._current_image.size)
        if not regions:
            self.statusBar().showMessage("検出されませんでした", 5000)
            return
        self.canvas.add_regions(regions)
        self.statusBar().showMessage(f"{len(regions)} 件の範囲を追加しました", 5000)

    def _on_detect_failed(self, message: str) -> None:
        self._detect_act.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "検出エラー", message)
```

`closeEvent` でワーカーを止める:

```python
    def closeEvent(self, event):
        if self._confirm_discard():
            self._worker.stop()
            self._settings.set_geometry(self.saveGeometry())
            event.accept()
        else:
            event.ignore()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS(既存テストも含めて全件)

- [ ] **Step 6: 全テストを流す**

Run: `python -m pytest -v`
Expected: PASS(全件)

- [ ] **Step 7: Commit**

```bash
git add mosaic_tool/app.py mosaic_tool/detect/setup_dialog.py tests/test_app.py
git commit -m "feat(app): ツールバーに自動検出とセットアップ導線を追加"
```

---

### Task 10: uv の同梱とドキュメント

配布物に `uv.exe` を含め、README に自動検出の使い方を追記する。ここまでのタスクで `paths.bundled_uv_path()` は「展開先ルートの `uv.exe`」を見ているため、`--add-data` の配置先はルート(`.`)にする。

**Files:**
- Modify: `scripts/build.ps1`(uv の取得と `--add-data`)、`README.md`、`docs/development.md`、`.gitignore`
- Test: 手動確認(`just package` の出力と、展開した zip からの起動)

**Interfaces:**
- Consumes: `paths.bundled_uv_path()`(Task 4)
- Produces: `dist\MosaicTool.exe` に `uv.exe` が同梱される

- [ ] **Step 1: build.ps1 に uv の取得を追加する**

`scripts/build.ps1` の `param(...)` に引数を追加する:

```powershell
    [string]$UvVersion = "latest",
```

`# ビルド本体` の直前に追加する:

```powershell
# 自動検出のセットアップに使う uv を取得して同梱する
# (ユーザーの環境に Python が無くても venv を用意できるようにするため)
$uvDir = Join-Path $repoRoot "build\uv"
$uvExe = Join-Path $uvDir "uv.exe"
if (-not (Test-Path -LiteralPath $uvExe)) {
    Write-Host "-- uv ($UvVersion) を取得します"
    New-Item -ItemType Directory -Path $uvDir -Force | Out-Null
    $uvAsset = "uv-x86_64-pc-windows-msvc.zip"
    $uvUrl = if ($UvVersion -eq "latest") {
        "https://github.com/astral-sh/uv/releases/latest/download/$uvAsset"
    } else {
        "https://github.com/astral-sh/uv/releases/download/$UvVersion/$uvAsset"
    }
    $uvZip = Join-Path $uvDir $uvAsset
    Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip
    Expand-Archive -LiteralPath $uvZip -DestinationPath $uvDir -Force
    Remove-Item -LiteralPath $uvZip -Force
}
if (-not (Test-Path -LiteralPath $uvExe)) {
    throw "uv.exe を取得できませんでした: $uvDir"
}
```

`$options` の `--add-data` の直後に 1 行追加する:

```powershell
    "--add-data", "${uvExe}:.",           # mosaic_tool/detect/paths.py が展開先ルートの uv.exe を参照する
```

- [ ] **Step 2: ビルドして同梱を確認する**

Run: `just build -OneDir`
Expected: `dist\MosaicTool\_internal\uv.exe`(または `dist\MosaicTool\uv.exe`)が存在する。確認コマンド:

```bash
ls dist/MosaicTool/_internal/uv.exe
```

- [ ] **Step 3: README に自動検出の節を追記する**

`README.md` の「保存先」の節の直後に追加する:

```markdown
## 自動検出 (任意)

YOLO 形式の検出モデルを用意すると、モザイク範囲を自動で追加できます。

1. `MosaicTool.exe` と同じ場所に `models` フォルダを作り、検出モデル (`.pt`) を置きます
2. ツールバーの「自動検出」(または `D` キー) を押します
3. 初回のみ、推論用の実行環境をダウンロードします (GPU 版 約 2.5GB / CPU 版 約 250MB)。
   同じ場所の `runtime` フォルダに入ります
4. 2 回目以降は押すだけで、検出した範囲が追加されます

検出された範囲は通常の範囲と同じように移動・変形・削除でき、`Ctrl+Z` 一回でまとめて取り消せます。
既に引いてある範囲は消えません。信頼度 (%) を下げると検出されやすくなります。

バージョンを更新するときは、旧フォルダの `runtime` と `models` を新しい展開先へコピーすると、
実行環境を再ダウンロードせずに済みます。
```

「操作」の表の末尾に 1 行追加する:

```markdown
| 自動検出 | 「自動検出」ボタン / D キー(要セットアップ) |
```

「ショートカットキー」の表にも 1 行追加する:

```markdown
| D | 自動検出を実行 |
```

- [ ] **Step 4: .gitignore と development.md を更新する**

`.gitignore` に追加する:

```
/models/
/runtime/
/build/uv/
```

`docs/development.md` のビルド手順の節に追記する:

```markdown
ビルド時に `uv.exe` を GitHub から取得して `build/uv/` にキャッシュし、exe へ同梱します
(自動検出の実行環境セットアップに使います)。特定バージョンに固定する場合は
`just build -UvVersion 0.5.0` のようにバージョンを渡します。
```

- [ ] **Step 5: 全テストを流す**

Run: `python -m pytest -v`
Expected: PASS(全件)

- [ ] **Step 6: Commit**

```bash
git add scripts/build.ps1 README.md docs/development.md .gitignore
git commit -m "build: uv を exe に同梱し自動検出の手順を追記"
```

---

## 手動確認(全タスク完了後)

自動テストでは ultralytics を動かさないため、実環境で以下を確認する。

1. `just package` で zip を作り、別フォルダへ展開する
2. 展開先に `models\` を作り、`pussyV2.pt` などの検出モデルを置く
3. `MosaicTool.exe` を起動し、画像を開いて「自動検出」を押す
4. セットアップダイアログで CPU 版を選び、完了するまで待つ
5. 検出された範囲が追加され、ドラッグで移動・変形できることを確認する
6. `Ctrl+Z` 一回で追加分がまとめて消えることを確認する
7. 2 枚目の画像で「自動検出」を押し、1 枚目より明らかに速いこと(ワーカーが常駐していること)を確認する
8. アプリを終了し、タスクマネージャに `python.exe` が残っていないことを確認する
9. `models\` を空にして起動し、「自動検出」で案内が出ることを確認する
