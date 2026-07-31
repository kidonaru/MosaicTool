# タイムラインウィンドウ実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 動画のモザイク範囲の区間指定を、カテゴリ別の行(ペン/矩形/自動検出)を持つ独立したタイムラインウィンドウで行えるようにする。

**Architecture:** 純関数のレーン詰めロジック(`video/lanes.py`)、QPainter 自前描画のタイムラインウィンドウ(`video/timeline_window.py`)、既存 `VideoSession` への `source` フィールド追加、`app.py` でのシグナル配線、の 4 層。既存の下部バーの区間バー(`IntervalStrip`)は最後に撤去する。

**Tech Stack:** Python 3.11+ / PySide6 / pytest(`QT_QPA_PLATFORM=offscreen`)

**Spec:** `docs/superpowers/specs/2026-07-30-timeline-window-design.md`

## Global Constraints

- コードのコメント・エラーメッセージは日本語で書く
- テストは `python -m pytest tests/<file> -v` で実行(各テストファイル冒頭で `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`)
- コミットメッセージは Conventional Commits 形式の日本語
- Region の同一性比較は `is` / `id()`(Region は dataclass で eq が構造比較になるため)
- フレーム区間は両端含み(`start <= frame <= end`)

---

### Task 1: VideoRegion.source(カテゴリ由来)

**Files:**
- Modify: `mosaic_tool/video/session.py`
- Test: `tests/test_video_session.py`

**Interfaces:**
- Produces:
  - `RegionSource(Enum)`: `PEN` / `RECT` / `AUTO`(`session.py` に定義)
  - `VideoRegion.source: RegionSource`。省略時は `region.kind` から導出
    (STROKE→PEN, RECT→RECT, POLYGON→AUTO)。
    `VideoSession.add_intervals` は常に `source=RegionSource.AUTO` を設定する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_session.py` に追加:

```python
class TestSource:
    def test_source_derived_from_kind(self):
        rect = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        stroke = Region(kind=RegionKind.STROKE, points=[], width=10.0)
        poly = Region(kind=RegionKind.POLYGON, points=[])
        assert VideoRegion(rect, 0, 0).source is RegionSource.RECT
        assert VideoRegion(stroke, 0, 0).source is RegionSource.PEN
        assert VideoRegion(poly, 0, 0).source is RegionSource.AUTO

    def test_add_intervals_marks_auto(self):
        session = make_session()
        session.add_intervals([Interval(0, 5, (0, 0, 10, 10), [])])
        assert session.regions[0].source is RegionSource.AUTO

    def test_explicit_source_kept(self):
        rect = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        vr = VideoRegion(rect, 0, 0, source=RegionSource.AUTO)
        assert vr.source is RegionSource.AUTO
```

import に `RegionSource` を追加すること
(`from mosaic_tool.video.session import RegionSource, VideoRegion, VideoSession`)。
Region/Interval のコンストラクタ引数名は `mosaic_tool/regions.py` /
`mosaic_tool/video/merge.py` の定義を確認して合わせること(STROKE は
`points` と `width` を持つ。Interval は `(start, end, bbox, polygon)`)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_video_session.py::TestSource -v`
Expected: FAIL(`source` が存在しない)

- [ ] **Step 3: 実装**

`session.py` の `VideoRegion` を変更:

```python
class RegionSource(Enum):
    """範囲の由来。タイムラインの行分類に使う"""

    PEN = "pen"
    RECT = "rect"
    AUTO = "auto"


# RegionKind からカテゴリ由来を導く対応。手描きの多角形は存在しないため
# POLYGON は自動検出とみなす
_SOURCE_BY_KIND = {
    RegionKind.STROKE: RegionSource.PEN,
    RegionKind.RECT: RegionSource.RECT,
    RegionKind.POLYGON: RegionSource.AUTO,
}


@dataclass
class VideoRegion:
    """モザイク範囲 1 個と適用区間(両端のフレームを含む)"""

    region: Region
    start: int
    end: int
    # タイムラインの行分類。省略時は形状から導出する
    source: RegionSource | None = None

    def __post_init__(self) -> None:
        if self.source is None:
            self.source = _SOURCE_BY_KIND[self.region.kind]

    def covers(self, frame: int) -> bool:
        return self.start <= frame <= self.end
```

(`from enum import Enum` を import に追加)

`add_intervals` 内の生成を `VideoRegion(_interval_region(iv, self.info), iv.start, iv.end, source=RegionSource.AUTO)` に変更。

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_video_session.py -v`
Expected: 全 PASS(既存テストは位置引数 3 個で構築しており default で互換)

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/session.py tests/test_video_session.py
git commit -m "feat(video): VideoRegion にカテゴリ由来 source を追加する"
```

---

### Task 2: レーン詰めロジック(純関数)

**Files:**
- Create: `mosaic_tool/video/lanes.py`
- Test: `tests/test_video_lanes.py`

**Interfaces:**
- Consumes: Task 1 の `RegionSource`
- Produces:
  - `CATEGORY_ORDER: list[RegionSource]` = `[PEN, RECT, AUTO]`
  - `CATEGORY_LABELS: dict[RegionSource, str]` = `{PEN: "ペン", RECT: "矩形", AUTO: "自動検出"}`
  - `pack_lanes(intervals: list[tuple[int, int]]) -> list[int]`
    (入力順を保ったまま各区間のレーン番号を返す。min-heap による
    区間パーティショニングで O(n log n)。自動検出でフレームごとの
    独立区間が数千個になっても速度が落ちない)
  - `TimelineLane` dataclass: `source: RegionSource`, `items: list[VideoRegion]`
  - `build_rows(regions: list[VideoRegion]) -> list[TimelineLane]`
    (カテゴリ順に、重ならない区間を同一レーンへ詰めた行リスト。空カテゴリは行なし)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_lanes.py` を新規作成:

```python
"""タイムラインのレーン詰めロジックの検証"""
from pathlib import Path

from PySide6.QtCore import QRectF

from mosaic_tool.regions import Region, RegionKind
from mosaic_tool.video.lanes import build_rows, pack_lanes
from mosaic_tool.video.session import RegionSource, VideoRegion


def vr(start, end, kind=RegionKind.RECT):
    region = Region(kind=kind, rect=QRectF(0, 0, 10, 10))
    return VideoRegion(region, start, end)


class TestPackLanes:
    def test_empty(self):
        assert pack_lanes([]) == []

    def test_non_overlapping_share_lane(self):
        assert pack_lanes([(0, 5), (6, 10), (11, 20)]) == [0, 0, 0]

    def test_overlapping_get_new_lane(self):
        assert pack_lanes([(0, 10), (5, 15)]) == [0, 1]

    def test_touching_edges_overlap(self):
        # 両端含みの区間なので end == start は重なりとして扱う
        assert pack_lanes([(0, 5), (5, 10)]) == [0, 1]

    def test_lane_reused_after_gap(self):
        # 最も早く終わったレーン(この場合 lane1)が再利用される
        assert pack_lanes([(0, 10), (5, 8), (20, 30)]) == [0, 1, 1]

    def test_input_order_preserved(self):
        # 開始順に並んでいなくても結果は入力順で返る
        assert pack_lanes([(20, 30), (0, 10)]) == [0, 0]

    def test_many_disjoint_intervals_fast(self):
        # 自動検出相当: フレームごとの独立区間 5000 個でも 1 レーンに詰まる
        lanes = pack_lanes([(i, i) for i in range(0, 10000, 2)])
        assert set(lanes) == {0}


class TestBuildRows:
    def test_grouped_by_category_in_order(self):
        regions = [
            vr(0, 5, RegionKind.POLYGON),   # auto
            vr(0, 5, RegionKind.STROKE),    # pen
            vr(0, 5, RegionKind.RECT),      # rect
        ]
        rows = build_rows(regions)
        assert [row.source for row in rows] == [
            RegionSource.PEN, RegionSource.RECT, RegionSource.AUTO,
        ]

    def test_empty_category_skipped(self):
        rows = build_rows([vr(0, 5, RegionKind.RECT)])
        assert [row.source for row in rows] == [RegionSource.RECT]

    def test_overlap_splits_into_lanes(self):
        regions = [vr(0, 10), vr(5, 15), vr(20, 30)]
        rows = build_rows(regions)
        assert len(rows) == 2
        assert [(r.start, r.end) for r in rows[0].items] == [(0, 10), (20, 30)]
        assert [(r.start, r.end) for r in rows[1].items] == [(5, 15)]
```

STROKE の Region 構築が `rect=` でエラーになる場合は `regions.py` の
定義に合わせて `points=[] , width=10.0` 等へ調整すること。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_video_lanes.py -v`
Expected: FAIL(`mosaic_tool.video.lanes` が存在しない)

- [ ] **Step 3: 実装**

`mosaic_tool/video/lanes.py` を新規作成:

```python
"""タイムラインの行構成: カテゴリ分類と重ならない区間のレーン詰め"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

from mosaic_tool.video.session import RegionSource, VideoRegion

# タイムラインの行の並び順と表示ラベル
CATEGORY_ORDER = [RegionSource.PEN, RegionSource.RECT, RegionSource.AUTO]
CATEGORY_LABELS = {
    RegionSource.PEN: "ペン",
    RegionSource.RECT: "矩形",
    RegionSource.AUTO: "自動検出",
}


@dataclass
class TimelineLane:
    """タイムラインの 1 行(同一カテゴリ内で重ならない区間の集まり)"""

    source: RegionSource
    items: list[VideoRegion]


def pack_lanes(intervals: list[tuple[int, int]]) -> list[int]:
    """重ならない区間を同じレーンへ詰め、入力順のレーン番号を返す

    開始フレーム順に見て「最も早く終わるレーン」へ min-heap で割り当てる
    区間パーティショニング O(n log n)。自動検出でフレームごとの独立区間が
    数千個並んでも実用速度を保つ。
    区間は両端含みのため、end == 次の start は重なりとして扱う。
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i])
    lanes = [0] * len(intervals)
    heap: list[tuple[int, int]] = []  # (レーン最後の終了フレーム, レーン番号)
    lane_count = 0
    for i in order:
        start, end = intervals[i]
        if heap and heap[0][0] < start:
            # 最も早く終わったレーンが空いていれば再利用する
            _, lane = heapq.heappop(heap)
        else:
            lane = lane_count
            lane_count += 1
        lanes[i] = lane
        heapq.heappush(heap, (end, lane))
    return lanes


def build_rows(regions: list[VideoRegion]) -> list[TimelineLane]:
    """カテゴリ順にレーン詰めした行リストを作る(空カテゴリは行を作らない)"""
    rows: list[TimelineLane] = []
    for source in CATEGORY_ORDER:
        group = [vr for vr in regions if vr.source is source]
        if not group:
            continue
        lanes = pack_lanes([(vr.start, vr.end) for vr in group])
        buckets: list[list[VideoRegion]] = [[] for _ in range(max(lanes) + 1)]
        for vr, lane in zip(group, lanes):
            buckets[lane].append(vr)
        rows.extend(TimelineLane(source, items) for items in buckets)
    return rows
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_video_lanes.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/lanes.py tests/test_video_lanes.py
git commit -m "feat(video): タイムライン行のレーン詰めロジックを追加する"
```

---

### Task 3: タイムラインウィンドウの表示(描画・座標・ズーム)

**Files:**
- Create: `mosaic_tool/video/timeline_window.py`
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 2 の `build_rows` / `TimelineLane` / `CATEGORY_LABELS`
- Produces:
  - `TimelineWindow(QWidget)`(`Qt.Window` フラグつき、タイトル「タイムライン」)
    - `set_total(total_frames: int) -> None`
    - `set_data(regions: list[VideoRegion], selected: Region | None) -> None`
    - `set_frame(frame: int) -> None`(再生ヘッド移動 + 可視範囲外なら自動スクロール)
    - シグナルは Task 4 で追加
  - 内部ウィジェット `TimelineArea(QWidget)`(テストからも直接使う)
    - `_x(frame: int) -> float` / `_frame_at(x: float) -> int`
    - `_px_per_frame: float`(既定 2.0)、`_zoom(factor, anchor_x)` で 0.05〜20.0 にクランプ
    - `_rows: list[TimelineLane]`
    - `_bar_rect(lane_index: int, vr: VideoRegion) -> QRectF`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` を新規作成:

```python
"""タイムラインウィンドウ(カテゴリ別の行と区間バー)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.regions import Region, RegionKind  # noqa: E402
from mosaic_tool.video.session import RegionSource, VideoRegion  # noqa: E402
from mosaic_tool.video.timeline_window import (  # noqa: E402
    LABEL_W,
    ROW_H,
    RULER_H,
    TimelineArea,
    TimelineWindow,
)


def vr(start, end, kind=RegionKind.RECT):
    region = Region(kind=kind, rect=QRectF(0, 0, 10, 10))
    return VideoRegion(region, start, end)


def make_area(total=100, ppf=2.0):
    QApplication.instance() or QApplication([])
    area = TimelineArea()
    area.set_total(total)
    area._px_per_frame = ppf
    return area


class TestMapping:
    def test_frame_to_x_and_back(self):
        area = make_area()
        # バー領域は LABEL_W から始まる
        assert area._x(0) == LABEL_W
        assert area._x(10) == LABEL_W + 20
        assert area._frame_at(LABEL_W + 20) == 10

    def test_frame_at_clamped(self):
        area = make_area(total=100)
        assert area._frame_at(-999) == 0
        assert area._frame_at(999999) == 99


class TestZoom:
    def test_zoom_clamped(self):
        area = make_area(ppf=19.0)
        area._zoom(2.0, anchor_x=0)
        assert area._px_per_frame == 20.0
        area._px_per_frame = 0.06
        area._zoom(0.5, anchor_x=0)
        assert area._px_per_frame == 0.05

    def test_zoom_changes_width(self):
        area = make_area(total=100, ppf=2.0)
        w1 = area.sizeHint().width()
        area._zoom(2.0, anchor_x=0)
        assert area.sizeHint().width() > w1


class TestRows:
    def test_set_data_builds_rows(self):
        area = make_area()
        area.set_data([vr(0, 10), vr(5, 15), vr(0, 5, RegionKind.STROKE)], None)
        # pen 1 行 + rect 2 行(重なりで分割)
        assert [row.source for row in area._rows] == [
            RegionSource.PEN, RegionSource.RECT, RegionSource.RECT,
        ]

    def test_bar_rect_geometry(self):
        area = make_area(ppf=2.0)
        item = vr(10, 19)
        area.set_data([item], None)
        rect = area._bar_rect(0, item)
        assert rect.left() == LABEL_W + 20
        # 両端含みなので幅は (19 - 10 + 1) * 2px
        assert rect.width() == 20
        assert rect.top() >= RULER_H

    def test_height_follows_row_count(self):
        area = make_area()
        area.set_data([vr(0, 10), vr(5, 15)], None)
        h2 = area.sizeHint().height()
        area.set_data([vr(0, 10)], None)
        assert area.sizeHint().height() < h2


class TestWindow:
    def test_window_flag_and_title(self):
        QApplication.instance() or QApplication([])
        window = TimelineWindow()
        assert window.windowTitle() == "タイムライン"
        assert window.isWindow()

    def test_set_frame_moves_playhead(self):
        QApplication.instance() or QApplication([])
        window = TimelineWindow()
        window.set_total(100)
        window.set_frame(42)
        assert window._area._frame == 42
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: FAIL(モジュールが存在しない)

- [ ] **Step 3: 実装**

`mosaic_tool/video/timeline_window.py` を新規作成。描画とジオメトリのみ
(マウス操作は Task 4)。骨子:

```python
"""動画モードのタイムラインウィンドウ(カテゴリ別の行と区間バー)"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from mosaic_tool.regions import Region
from mosaic_tool.video.lanes import CATEGORY_LABELS, TimelineLane, build_rows
from mosaic_tool.video.session import VideoRegion

# レイアウト定数 (px)
LABEL_W = 72   # 左端のカテゴリラベル列(スクロールに追従して固定表示)
RULER_H = 20   # 上端のルーラー
ROW_H = 18     # 行の高さ
ROW_GAP = 2    # 行間

# ズームの範囲 (px/フレーム) と 1 ノッチの倍率
ZOOM_MIN = 0.05
ZOOM_MAX = 20.0
ZOOM_STEP = 1.25

_BAR_COLOR = QColor(100, 150, 240, 160)
_SELECTED_COLOR = QColor(60, 120, 255, 230)
_PLAYHEAD_COLOR = QColor(255, 80, 80, 220)
_LABEL_BG = QColor(45, 45, 45)


class TimelineArea(QWidget):
    """タイムライン本体。ルーラー・カテゴリ行・区間バー・再生ヘッドを自前描画する"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0
        self._frame = 0
        self._rows: list[TimelineLane] = []
        self._selected: Region | None = None
        self._px_per_frame = 2.0
        self._scroll_x = 0  # ラベル列の固定表示に使う水平スクロール量

    # --- データ更新 ---

    def set_total(self, total: int) -> None: ...
    def set_data(self, regions: list[VideoRegion], selected: Region | None) -> None:
        # build_rows で行を作り直し、updateGeometry() + update()
        ...
    def set_frame(self, frame: int) -> None: ...
    def set_scroll_x(self, x: int) -> None: ...

    # --- 座標変換とジオメトリ ---

    def _x(self, frame: int) -> float:
        return LABEL_W + frame * self._px_per_frame

    def _frame_at(self, x: float) -> int:
        if self._total <= 0:
            return 0
        frame = int((x - LABEL_W) / self._px_per_frame)
        return max(0, min(self._total - 1, frame))

    def _row_top(self, lane_index: int) -> float:
        return RULER_H + ROW_GAP + lane_index * (ROW_H + ROW_GAP)

    def _bar_rect(self, lane_index: int, vr: VideoRegion) -> QRectF:
        x1 = self._x(vr.start)
        # 両端含みの区間なので終了フレームの右端まで塗る
        x2 = self._x(vr.end + 1)
        return QRectF(x1, self._row_top(lane_index), max(3.0, x2 - x1), ROW_H)

    def sizeHint(self):  # noqa: N802 (Qt のオーバーライド)
        # 幅: ラベル列 + 全フレーム。高さ: ルーラー + 全行
        ...

    def _zoom(self, factor: float, anchor_x: float) -> None:
        # px/フレームを factor 倍して ZOOM_MIN..ZOOM_MAX にクランプし、
        # updateGeometry()。anchor 位置の補正は TimelineWindow 側で行う
        ...

    # --- 描画 ---

    def paintEvent(self, event) -> None:
        # 1. ルーラー: 目盛り(適当な丸め間隔でフレーム番号)を描画
        # 2. 各行: バー(選択中は _SELECTED_COLOR + 両端に白ハンドル)。
        #    自動検出はフレームごとの区間で数千個になり得るため、
        #    event.rect() に掛かるバーだけ描く(可視フレーム範囲を
        #    _frame_at(rect.left()) 〜 _frame_at(rect.right()) で求めて絞る)
        # 3. 再生ヘッド: _x(_frame) の縦線をルーラーから最下段まで
        # 4. ラベル列: x = _scroll_x に _LABEL_BG で塗った帯を重ね、
        #    カテゴリの先頭行(前の行と source が異なる行)にラベルを
        #    CATEGORY_LABELS[source] で描く
        ...


class TimelineWindow(QWidget):
    """タイムラインウィンドウ本体。TimelineArea を横スクロール領域に載せる"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setWindowTitle("タイムライン")
        self.resize(900, 220)
        self._area = TimelineArea()
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._area)
        self._scroll.setWidgetResizable(True)
        self._scroll.horizontalScrollBar().valueChanged.connect(
            self._area.set_scroll_x
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    def set_total(self, total: int) -> None: ...
    def set_data(self, regions, selected) -> None: ...

    def set_frame(self, frame: int) -> None:
        # 再生ヘッドを移動し、可視範囲から外れたらスクロールで追従する
        self._area.set_frame(frame)
        x = self._area._x(frame)
        bar = self._scroll.horizontalScrollBar()
        view_w = self._scroll.viewport().width()
        if not (bar.value() + LABEL_W <= x <= bar.value() + view_w):
            bar.setValue(int(x - view_w * 0.2))
```

注意点:
- `setWidgetResizable(True)` だと sizeHint が無視されるため、実装時は
  `setWidgetResizable(False)` + `_area.resize(_area.sizeHint())` を
  データ/ズーム更新のたびに呼ぶ方式にする(どちらでも良いが横幅が
  フレーム数 × px/フレームに追従することをテストで担保する)
- ラベル列はコンテンツ座標 `x = _scroll_x` に描くことで見かけ上固定になる
- 目盛り間隔は「ラベルが約 80px 間隔になる 1/2/5×10^n 系列」で丸める

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(video): カテゴリ別の行を持つタイムラインウィンドウを追加する"
```

---

### Task 4: タイムラインウィンドウのマウス・キー操作

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Produces(`TimelineWindow` と `TimelineArea` の両方に同名シグナル。
  Window は Area のシグナルを中継する):
  - `seek_requested = Signal(int)` — ルーラーのクリック/ドラッグ
  - `interval_edited = Signal(object, int, int)` — (Region, start, end)。端ドラッグ・バー移動中に逐次発火
  - `region_clicked = Signal(object)` — (Region)。バークリック
  - `delete_requested = Signal(object)` — (Region)。選択中バーがある状態での Delete/Backspace
- ヒット判定(テスト対象): `_edge_at(pos) -> tuple[VideoRegion, str] | None`
  (`"start"` / `"end"`、判定幅 ±5px)、`_bar_at(pos) -> VideoRegion | None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` に追加:

```python
from PySide6.QtCore import QPointF  # noqa: E402


def press(area, x, y):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    area.mousePressEvent(event)


def move(area, x, y):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    area.mouseMoveEvent(event)


class TestHit:
    def test_edge_at_selected_bar(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], item.region)
        y = area._row_top(0) + 5
        hit = area._edge_at(QPointF(area._x(10), y))
        assert hit == (item, "start")
        hit = area._edge_at(QPointF(area._x(21), y))
        assert hit == (item, "end")

    def test_bar_at(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], None)
        y = area._row_top(0) + 5
        assert area._bar_at(QPointF(area._x(15), y)) is item
        assert area._bar_at(QPointF(area._x(50), y)) is None


class TestDrag:
    def test_edge_drag_edits_interval(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], item.region)
        fired = []
        area.interval_edited.connect(lambda r, s, e: fired.append((s, e)))
        y = area._row_top(0) + 5
        press(area, area._x(21), y)       # 終端をつかむ
        move(area, area._x(31), y)        # 終端を 30 まで伸ばす
        assert fired[-1] == (10, 30)
        assert (item.start, item.end) == (10, 30)

    def test_move_drag_keeps_length(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], item.region)
        fired = []
        area.interval_edited.connect(lambda r, s, e: fired.append((s, e)))
        y = area._row_top(0) + 5
        press(area, area._x(15), y)       # バー中央をつかむ
        move(area, area._x(20), y)        # 右へ 5 フレーム
        assert fired[-1] == (15, 25)

    def test_move_drag_clamped_at_start(self):
        area = make_area(ppf=2.0)
        item = vr(2, 6)
        area.set_data([item], item.region)
        y = area._row_top(0) + 5
        press(area, area._x(4), y)
        move(area, area._x(0) - 100, y)   # 左端より外へ
        assert (item.start, item.end) == (0, 4)


class TestClickAndSeek:
    def test_bar_click_emits_region(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], None)
        fired = []
        area.region_clicked.connect(fired.append)
        press(area, area._x(15), area._row_top(0) + 5)
        assert fired == [item.region]

    def test_ruler_press_and_drag_seeks(self):
        area = make_area(ppf=2.0)
        fired = []
        area.seek_requested.connect(fired.append)
        press(area, area._x(30), RULER_H / 2)
        move(area, area._x(40), RULER_H / 2)
        assert fired == [30, 40]


class TestDelete:
    def test_delete_key_emits_selected(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], item.region)
        fired = []
        area.delete_requested.connect(fired.append)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Delete,
            Qt.KeyboardModifier.NoModifier,
        )
        area.keyPressEvent(event)
        assert fired == [item.region]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: 新規テストが FAIL(シグナル・ヒット判定が未実装)

- [ ] **Step 3: 実装**

`TimelineArea` へ追加する。要点:

- ドラッグ状態: `self._drag: tuple[str, VideoRegion] | None`
  (`("start", vr)` / `("end", vr)` / `("move", vr)` / `("seek", None)`)。
  move 用に `self._drag_offset: int`(つかんだ位置と start の差)も保持
- `mousePressEvent`:
  - ルーラー内(`y < RULER_H`)→ `seek_requested.emit(_frame_at(x))` + seek ドラッグ開始
  - `_edge_at` ヒット → 端ドラッグ開始(選択中バーのみ対象)
  - `_bar_at` ヒット → `region_clicked.emit(vr.region)` + move ドラッグ開始
- `mouseMoveEvent`:
  - 端: 反対側の端でクランプして `vr.start` / `vr.end` を更新し
    `interval_edited.emit(vr.region, vr.start, vr.end)` + `update()`
  - move: 長さ維持で `0..total-1-length` にクランプして平行移動 + 同シグナル
  - seek: `seek_requested.emit(_frame_at(x))`
- `mouseReleaseEvent`: `_drag = None`。move/端ドラッグ後は行のレーン詰めが
  変わりうるため `set_data` 相当の再構築は app 側からの次回更新に任せる
  (ドラッグ中は自行内で描画更新のみ)
- `keyPressEvent`: Delete/Backspace で選択中の region があれば
  `delete_requested.emit(region)`。フォーカスを受けるよう
  `setFocusPolicy(Qt.FocusPolicy.ClickFocus)`
- `wheelEvent`: Ctrl 押下時のみ `_zoom(ZOOM_STEP or 1/ZOOM_STEP, x)`。
  `TimelineWindow` 側でアンカー補正:
  `bar.setValue(int(frame_under_cursor * new_ppf + LABEL_W - cursor_viewport_x))`
- `TimelineWindow.__init__` で Area の 4 シグナルを同名シグナルへ connect する

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(video): タイムラインウィンドウの操作(伸縮・移動・選択・削除・シーク)を追加する"
```

---

### Task 5: キャンバスの範囲削除 API

**Files:**
- Modify: `mosaic_tool/canvas.py`(keyPressEvent の削除処理を公開メソッドへ抽出)
- Test: `tests/test_canvas.py`

**Interfaces:**
- Produces: `MosaicCanvas.delete_regions(regions: list[Region]) -> None`
  — 一致する RegionItem をシーンから外し、Undo スタックへ積み、
  `regions_changed` を発火する。一致判定は `is`(同一インスタンス)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_canvas.py` の既存のフィクスチャ/ヘルパー(キャンバス生成と
画像セットのやり方)を確認し、それに合わせて追加する:

```python
def test_delete_regions_removes_and_undoable(canvas_with_image):
    canvas = canvas_with_image
    region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
    canvas.add_region(region)
    fired = []
    canvas.regions_changed.connect(lambda: fired.append(True))
    canvas.delete_regions([region])
    assert canvas.get_regions() == []
    assert fired
    canvas.undo()
    assert len(canvas.get_regions()) == 1


def test_delete_regions_ignores_unknown(canvas_with_image):
    canvas = canvas_with_image
    known = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
    canvas.add_region(known)
    unknown = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
    canvas.delete_regions([unknown])  # 構造が同じでも別インスタンスは消さない
    assert len(canvas.get_regions()) == 1
```

(`canvas_with_image` に相当するフィクスチャが無ければ既存テストの
生成コードを流用してヘルパーを作る)

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_canvas.py -k delete_regions -v`
Expected: FAIL(`delete_regions` が存在しない)

- [ ] **Step 3: 実装**

`canvas.py` に公開メソッドを追加し、`keyPressEvent` の削除分岐を
これで置き換える:

```python
def delete_regions(self, regions: list[Region]) -> None:
    """指定した範囲(同一インスタンス)をシーンから削除する(Undo 可能)"""
    targets = [
        it for it in self._region_items()
        if any(it.region is r for r in regions)
    ]
    if not targets:
        return
    for it in targets:
        self._scene.removeItem(it)
    # 参照を保持したままスタックへ(Undo で戻せるようにする)
    self._undo_stack.append(("remove", targets))
    self._refresh_overlay()
```

`keyPressEvent` の Delete 分岐は
`self.delete_regions([it.region for it in ... selectedItems ...])` に差し替える。

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_canvas.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/canvas.py tests/test_canvas.py
git commit -m "refactor(canvas): 範囲削除を公開メソッド delete_regions へ抽出する"
```

---

### Task 6: app 統合(ウィンドウの表示・シグナル配線)

**Files:**
- Modify: `mosaic_tool/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `TimelineWindow`(Task 3/4)、`MosaicCanvas.delete_regions`(Task 5)、
  `TimelineBar.seek`(本タスクで追加)
- Produces:
  - `TimelineBar.seek(frame: int) -> None`(`mosaic_tool/video/timeline.py` に追加。
    `self._slider.setValue(frame)` のみ。`frame_changed` を発火させてシーク一式を通す)
  - `MainWindow._timeline_window: TimelineWindow | None`(遅延生成)
  - ツールバーに「タイムライン」アクション(動画モード時のみ有効。押すと再表示)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_app.py` に動画モードのフィクスチャとテストを追加。
既存の `window` フィクスチャ(offscreen)を利用する:

```python
import io as std_io

from PIL import Image as PILImage

from mosaic_tool.video.ffmpeg import VideoInfo


@pytest.fixture
def video(window, monkeypatch, tmp_path):
    """ffmpeg をモックして動画モードへ入れる"""
    info = VideoInfo(64, 48, 30.0, "30/1", 100, 3.3, None)
    buf = std_io.BytesIO()
    PILImage.new("RGB", (64, 48)).save(buf, "PNG")
    monkeypatch.setattr(
        "mosaic_tool.app.video_ffmpeg.is_ffmpeg_ready", lambda: True
    )
    monkeypatch.setattr("mosaic_tool.app.video_ffmpeg.probe", lambda p: info)
    monkeypatch.setattr(
        "mosaic_tool.app.video_ffmpeg.extract_frame",
        lambda *a, **k: buf.getvalue(),
    )
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"")
    window._open_video(path)
    return window


class TestTimelineWindowIntegration:
    def test_open_video_shows_timeline_window(self, video):
        assert video._timeline_window is not None
        assert video._timeline_window.isVisible()

    def test_leave_video_hides_timeline_window(self, video):
        video._leave_video_mode()
        assert not video._timeline_window.isVisible()

    def test_seek_from_window_moves_bottom_bar(self, video):
        video._timeline_window.seek_requested.emit(30)
        assert video._timeline.frame() == 30
        assert video._video.frame == 30

    def test_interval_edit_marks_dirty(self, video):
        region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        video.canvas.add_region(region)      # sync で現在フレームの区間になる
        video._dirty = False
        vr = video._video.find(region)
        video._timeline_window.interval_edited.emit(region, 0, 50)
        assert (vr.start, vr.end) == (0, 50)
        assert video._dirty

    def test_delete_from_window_removes_region(self, video):
        region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        video.canvas.add_region(region)
        video._timeline_window.delete_requested.emit(region)
        assert video._video.find(region) is None
        assert video.canvas.get_regions() == []

    def test_delete_offscreen_region_removes_from_session(self, video):
        # 現在フレーム(0)に掛からない範囲はキャンバスに無くても消せる
        region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        video._video.regions.append(VideoRegion(region, 50, 60))
        video._timeline_window.delete_requested.emit(region)
        assert video._video.find(region) is None

    def test_region_click_selects_on_canvas(self, video):
        region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        video.canvas.add_region(region)
        video.canvas.select_regions([])
        video._timeline_window.region_clicked.emit(region)
        assert video.canvas.selected_regions() == [region]
```

import(`Region` / `RegionKind` / `QRectF` / `VideoRegion` / `pytest`)は
test_app.py の既存 import に合わせて過不足なく追加する。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_app.py::TestTimelineWindowIntegration -v`
Expected: FAIL(`_timeline_window` が存在しない)

- [ ] **Step 3: 実装**

1. `timeline.py` の `TimelineBar` に追加:

```python
def seek(self, frame: int) -> None:
    """外部(タイムラインウィンドウ)からのシーク。frame_changed を発火する"""
    self._slider.setValue(frame)
```

2. `app.py` の `__init__` に `self._timeline_window: TimelineWindow | None = None`
   を追加し、以下のメソッドを実装:

```python
def _ensure_timeline_window(self) -> TimelineWindow:
    """タイムラインウィンドウを遅延生成して返す"""
    if self._timeline_window is None:
        window = TimelineWindow(self)
        window.seek_requested.connect(self._timeline.seek)
        window.interval_edited.connect(self._on_timeline_interval_edited)
        window.region_clicked.connect(self._on_timeline_region_clicked)
        window.delete_requested.connect(self._on_timeline_delete)
        self._timeline_window = window
    return self._timeline_window

def _show_timeline_window(self) -> None:
    if self._video is None:
        return
    window = self._ensure_timeline_window()
    window.set_total(self._video.info.frame_count)
    self._update_timeline_window()
    window.set_frame(self._video.frame)
    window.show()
    window.raise_()

def _update_timeline_window(self) -> None:
    """全区間と選択状態をタイムラインウィンドウへ反映する"""
    if self._timeline_window is None or self._video is None:
        return
    selected = self.canvas.selected_regions()
    self._timeline_window.set_data(
        self._video.regions, selected[0] if len(selected) == 1 else None
    )

def _on_timeline_interval_edited(self, region, start: int, end: int) -> None:
    video = self._video
    vr = video.find(region) if video is not None else None
    if vr is None:
        return
    vr.start, vr.end = start, end
    self._dirty = True

def _on_timeline_region_clicked(self, region) -> None:
    video = self._video
    vr = video.find(region) if video is not None else None
    if vr is not None:
        self._select_video_region(vr)

def _on_timeline_delete(self, region) -> None:
    video = self._video
    vr = video.find(region) if video is not None else None
    if vr is None:
        return
    if id(region) in self._video_displayed_ids:
        # キャンバスに出ている範囲はキャンバス経由で消す
        # (regions_changed → sync で区間リストからも外れる)
        self.canvas.delete_regions([region])
    else:
        video.regions.remove(vr)
        self._dirty = True
        self._update_timeline_window()
```

3. 既存の `_on_interval_clicked(index)` の本体を
   `_select_video_region(vr: VideoRegion)` へ抽出し、index 版は
   `vr = video.regions[index]` を引いて委譲する(Task 7 で index 版を削除)。

4. 既存コードへのフック:
   - `_open_video`: 成功時の末尾で `self._show_timeline_window()`
   - `_leave_video_mode`: `if self._timeline_window is not None: self._timeline_window.hide()`
   - `_update_selection_interval` の末尾に `self._update_timeline_window()`
   - `_on_frame_changed` の末尾に
     `if self._timeline_window is not None: self._timeline_window.set_frame(frame)`
   - ツールバー(自動検出アクションの後)に:

```python
self._timeline_act = QAction("タイムライン", self)
self._timeline_act.setToolTip("タイムラインウィンドウを表示する(動画モードのみ)")
self._timeline_act.triggered.connect(self._show_timeline_window)
self._timeline_act.setEnabled(False)
tb.addAction(self._timeline_act)
```

   `_open_video` で `setEnabled(True)`、`_leave_video_mode` で `setEnabled(False)`。

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_app.py -v`
Expected: 全 PASS(既存テストも含む)

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/app.py mosaic_tool/video/timeline.py tests/test_app.py
git commit -m "feat(video): タイムラインウィンドウをアプリへ統合する"
```

---

### Task 7: 下部バーの区間バー撤去

**Files:**
- Modify: `mosaic_tool/video/timeline.py`(IntervalStrip と関連 UI を削除)
- Modify: `mosaic_tool/app.py`(旧シグナル配線と旧ハンドラを削除)
- Test: `tests/test_video_timeline.py`(IntervalStrip のテストを削除)

**Interfaces:**
- Consumes: Task 6 完了状態(ウィンドウ側で全操作が可能)
- Produces: `TimelineBar` はシーク・コマ送り・フレーム表示・検出間隔・`seek()` のみを持つ

- [ ] **Step 1: テストを先に更新する**

`tests/test_video_timeline.py` から `IntervalStrip` 関連
(`make_strip`、`TestStripMapping`、`TestStripHit`、`TestIntervalLabel`、
import の `IntervalStrip`)を削除し、`TimelineBar` に区間 API が
残っていないことを確認するテストを追加:

```python
def test_interval_api_removed():
    bar = make_bar()
    assert not hasattr(bar, "set_intervals")
    assert not hasattr(bar, "interval_edited")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_video_timeline.py -v`
Expected: `test_interval_api_removed` が FAIL(まだ存在する)

- [ ] **Step 3: 実装**

- `timeline.py`: `IntervalStrip` クラス、`STRIP_HEIGHT` / `HANDLE_PX` /
  色定数、`TimelineBar` の `_strip` / `_interval_label` /
  `interval_edited` / `interval_clicked` / `set_intervals` /
  `_on_interval_edited` / `_update_interval_label` を削除。
  スライダーを直接 `layout.addWidget(self._slider, 1)` に戻す
- `app.py`:
  - `__init__` の `interval_edited` / `interval_clicked` の connect を削除
  - `_on_interval_edited` / `_on_interval_clicked` を削除
    (`_select_video_region` は残す)
  - `_leave_video_mode` の `set_intervals([], None)` を削除
  - `_update_selection_interval` を `_update_timeline_window()` を呼ぶだけに
    簡約し、メソッド名を `_update_selection_interval` のまま残すか
    呼び出し側ごと `_update_timeline_window` へ付け替える(付け替え推奨:
    `selection_changed` の connect 先と `_on_regions_changed` /
    `_on_frame_changed` / `_on_detected` 系の呼び出しを全部置換して
    `_update_selection_interval` を削除する)

- [ ] **Step 4: 全テストが通ることを確認**

Run: `python -m pytest tests/ -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline.py mosaic_tool/app.py tests/test_video_timeline.py
git commit -m "refactor(video): 下部バーの区間バーをタイムラインウィンドウへ置き換える"
```

---

### Task 8: ドキュメント更新

**Files:**
- Modify: `README.md`(動画モードの操作説明)

**Interfaces:**
- Consumes: Task 1〜7 の完成した挙動

- [ ] **Step 1: README の動画モードの節を更新**

区間バー(下部)の説明を削除し、タイムラインウィンドウの説明へ置き換える。
記載する操作:

- 動画を開くとタイムラインウィンドウが自動表示(閉じてもツールバーの
  「タイムライン」で再表示)
- 行はカテゴリ単位(ペン / 矩形 / 自動検出)。重ならない区間は同じ行に
  まとまり、重なる場合だけ行が増える
- バーの端ドラッグ: 区間の伸縮 / バー中央ドラッグ: 区間の平行移動
- バークリック: 対応する範囲を選択 / Delete: 選択中の範囲を削除
- ルーラーのクリック・ドラッグ: シーク / Ctrl+ホイール: 横ズーム

既存の README の文体・見出しレベルに合わせること。

- [ ] **Step 2: 実際の挙動と一致しているか確認**

実装済みコードと読み合わせ、齟齬がないことを確認する
(`just run <動画ファイル>` で目視確認できればなお良い)。

- [ ] **Step 3: コミット**

```bash
git add README.md
git commit -m "docs: 動画モードのタイムラインウィンドウの操作説明を追加する"
```
