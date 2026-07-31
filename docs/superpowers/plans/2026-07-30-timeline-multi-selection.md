# タイムラインの選択と区間編集の改修 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 動画モードのタイムラインで、区間バーの境目を線で示し、選択を確実に強調し、矩形選択で複数の区間をまとめて選んで削除・平行移動・リサイズできるようにする。

**Architecture:** 選択状態の持ち主をキャンバスから `TimelineArea` へ移す。選択集合と一括編集のクランプ計算は Qt に依存しない新モジュール `mosaic_tool/video/timeline_selection.py` に置き、`TimelineArea` は描画・ヒット判定・イベント振り分けに専念する。キャンバスへはベストエフォートで反映し、キャンバス側からの空の選択通知は無視してシーン再構築に巻き込まれないようにする。

**Tech Stack:** Python 3, PySide6 (Qt Widgets), pytest（`QT_QPA_PLATFORM=offscreen`）

**Spec:** `docs/superpowers/specs/2026-07-30-timeline-multi-selection-design.md`

## Global Constraints

- コードのコメントとエラーログメッセージは**日本語**で書く。
- 既存ファイルの記述スタイル（docstring の日本語、コメント密度、定数の命名）に合わせる。
- ハードコーディングは避け、レイアウト値・配色はモジュール先頭の定数として定義する。
- テストは `python -m pytest` で実行する。`tests/conftest.py` が `QT_QPA_PLATFORM=offscreen` を設定するため、GUI 無しで動く。
- 純ロジックは Qt ウィジェットに依存させない（`video/lanes.py` と同じ方針）。
- `VideoRegion` の同一性は**インスタンス比較**（`is` / `id()`）で判定する。`VideoRegion` と `Region` は dataclass のため `==` はフィールド比較になり、別個の区間を同一視してしまう。

---

### Task 1: 選択集合と一括編集の純ロジック

`TimelineSelection`（選択中 `VideoRegion` の集合）と、一括編集のデルタを丸める `clamp_delta` / 当てる `apply_delta` を新モジュールに作る。Qt ウィジェットには一切触らない。

**Files:**
- Create: `mosaic_tool/video/timeline_selection.py`
- Test: `tests/test_video_timeline_selection.py`

**Interfaces:**
- Consumes: `mosaic_tool.video.session.VideoRegion`（`region` / `start` / `end` を持つ dataclass）
- Produces:
  - 定数 `MOVE = "move"`, `START = "start"`, `END = "end"`
  - `class TimelineSelection`: `items() -> list[VideoRegion]`, `regions() -> list[Region]`, `__len__() -> int`, `contains(vr) -> bool`, `replace(items)`, `add(items)`, `toggle(vr)`, `clear()`, `prune(regions)`
  - `clamp_delta(items: list[VideoRegion], kind: str, delta: int, max_frame: int) -> int`
  - `apply_delta(items: list[VideoRegion], kind: str, delta: int) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_selection.py` を新規作成する。

```python
"""タイムラインの選択集合と一括編集(Qt に依存しない純ロジック)の検証"""
from PySide6.QtCore import QRectF

from mosaic_tool.regions import Region, RegionKind
from mosaic_tool.video.session import VideoRegion
from mosaic_tool.video.timeline_selection import (
    END,
    MOVE,
    START,
    TimelineSelection,
    apply_delta,
    clamp_delta,
)


def vr(start, end):
    # 同じ矩形でも別インスタンスにして、同一性がフィールド比較でないことを確かめる
    region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
    return VideoRegion(region, start, end)


class TestSelectionSet:
    def test_replace_and_items_keep_order(self):
        a, b = vr(0, 5), vr(10, 15)
        sel = TimelineSelection()
        sel.replace([b, a])
        assert sel.items() == [b, a]
        assert len(sel) == 2

    def test_contains_uses_identity(self):
        a = vr(0, 5)
        twin = vr(0, 5)   # 値は同じだが別インスタンス
        sel = TimelineSelection()
        sel.replace([a])
        assert sel.contains(a)
        assert not sel.contains(twin)

    def test_replace_drops_duplicates(self):
        a = vr(0, 5)
        sel = TimelineSelection()
        sel.replace([a, a])
        assert sel.items() == [a]

    def test_add_appends_without_duplicates(self):
        a, b = vr(0, 5), vr(10, 15)
        sel = TimelineSelection()
        sel.replace([a])
        sel.add([a, b])
        assert sel.items() == [a, b]

    def test_toggle_adds_then_removes(self):
        a = vr(0, 5)
        sel = TimelineSelection()
        sel.toggle(a)
        assert sel.items() == [a]
        sel.toggle(a)
        assert sel.items() == []

    def test_clear_empties(self):
        sel = TimelineSelection()
        sel.replace([vr(0, 5)])
        sel.clear()
        assert len(sel) == 0

    def test_regions_returns_underlying_regions(self):
        a, b = vr(0, 5), vr(10, 15)
        sel = TimelineSelection()
        sel.replace([a, b])
        assert sel.regions() == [a.region, b.region]

    def test_prune_keeps_only_living_intervals(self):
        a, b = vr(0, 5), vr(10, 15)
        sel = TimelineSelection()
        sel.replace([a, b])
        sel.prune([a])       # b はセッションから消えた
        assert sel.items() == [a]


class TestClampMove:
    def test_within_range_passes_through(self):
        items = [vr(10, 20), vr(30, 40)]
        assert clamp_delta(items, MOVE, 5, 99) == 5

    def test_stops_at_frame_zero_for_the_earliest(self):
        items = [vr(3, 8), vr(30, 40)]
        # 最も早い区間が 0 に当たるので全体が -3 で止まる
        assert clamp_delta(items, MOVE, -50, 99) == -3

    def test_stops_at_last_frame_for_the_latest(self):
        items = [vr(10, 20), vr(90, 95)]
        # 最も遅い区間が 99 に当たるので全体が +4 で止まる
        assert clamp_delta(items, MOVE, 50, 99) == 4

    def test_apply_shifts_every_item_by_the_same_amount(self):
        a, b = vr(10, 20), vr(30, 40)
        apply_delta([a, b], MOVE, 5)
        assert (a.start, a.end) == (15, 25)
        assert (b.start, b.end) == (35, 45)


class TestClampStart:
    def test_stops_at_frame_zero(self):
        items = [vr(2, 20)]
        assert clamp_delta(items, START, -50, 99) == -2

    def test_stops_at_its_own_end(self):
        items = [vr(10, 20), vr(30, 33)]
        # 短い方(幅 3)が先に終了へ当たるので全体が +3 で止まる
        assert clamp_delta(items, START, 50, 99) == 3

    def test_apply_moves_only_start(self):
        a = vr(10, 20)
        apply_delta([a], START, 3)
        assert (a.start, a.end) == (13, 20)


class TestClampEnd:
    def test_stops_at_last_frame(self):
        items = [vr(10, 20), vr(90, 95)]
        assert clamp_delta(items, END, 50, 99) == 4

    def test_stops_at_its_own_start(self):
        items = [vr(10, 20), vr(30, 33)]
        # 短い方(幅 3)が先に開始へ当たるので全体が -3 で止まる
        assert clamp_delta(items, END, -50, 99) == -3

    def test_apply_moves_only_end(self):
        a = vr(10, 20)
        apply_delta([a], END, -3)
        assert (a.start, a.end) == (10, 17)


class TestClampEdges:
    def test_empty_selection_yields_zero(self):
        assert clamp_delta([], MOVE, 10, 99) == 0

    def test_impossible_range_yields_zero(self):
        # 末尾を越えた区間が混ざると下限が上限を上回るため動かさない
        items = [vr(0, 5), vr(90, 200)]
        assert clamp_delta(items, MOVE, 5, 99) == 0
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_selection.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'mosaic_tool.video.timeline_selection'`）

- [ ] **Step 3: 実装を書く**

`mosaic_tool/video/timeline_selection.py` を新規作成する。

```python
"""タイムラインの選択集合と一括編集(Qt に依存しない純ロジック)

選択の持ち主はタイムライン(video/timeline_window.py)で、ここは集合の出入りと
一括編集の移動量の計算だけを担う。ウィジェット無しで検証できるように分けている。
"""
from __future__ import annotations

from mosaic_tool.regions import Region
from mosaic_tool.video.session import VideoRegion

# 一括編集の種類。move は平行移動、start / end は片側の端の伸び縮み
MOVE = "move"
START = "start"
END = "end"


def _unique(items: list[VideoRegion]) -> list[VideoRegion]:
    """同一インスタンスの重複を最初の出現だけ残して除く"""
    seen: set[int] = set()
    out: list[VideoRegion] = []
    for vr in items:
        if id(vr) not in seen:
            seen.add(id(vr))
            out.append(vr)
    return out


class TimelineSelection:
    """選択中の区間の集合

    VideoRegion と Region は dataclass のため == はフィールド比較になり、
    値の同じ別区間を同一視してしまう。よって同一インスタンス比較で持つ。
    選んだ順を保ち、削除や一括編集の対象順が見た目と食い違わないようにする。
    """

    def __init__(self) -> None:
        self._items: list[VideoRegion] = []

    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[VideoRegion]:
        return list(self._items)

    def regions(self) -> list[Region]:
        """選択中の区間が指すモザイク範囲(キャンバスへの反映用)"""
        return [vr.region for vr in self._items]

    def contains(self, vr: VideoRegion) -> bool:
        return any(v is vr for v in self._items)

    def replace(self, items: list[VideoRegion]) -> None:
        self._items = _unique(items)

    def add(self, items: list[VideoRegion]) -> None:
        self._items = _unique(self._items + list(items))

    def toggle(self, vr: VideoRegion) -> None:
        if self.contains(vr):
            self._items = [v for v in self._items if v is not vr]
        else:
            self._items.append(vr)

    def clear(self) -> None:
        self._items = []

    def prune(self, regions: list[VideoRegion]) -> None:
        """セッションに残っている区間だけを選択に残す"""
        alive = {id(vr) for vr in regions}
        self._items = [v for v in self._items if id(v) in alive]


def _delta_limits(
    items: list[VideoRegion], kind: str, max_frame: int
) -> tuple[int, int]:
    """選択全体が収まる移動量の下限と上限"""
    if kind == MOVE:
        return max(-vr.start for vr in items), min(max_frame - vr.end for vr in items)
    if kind == START:
        # 0 を下回らず、自分の終了フレームを越えない
        return (
            max(-vr.start for vr in items),
            min(vr.end - vr.start for vr in items),
        )
    # 自分の開始フレームを下回らず、末尾フレームを越えない
    return (
        max(vr.start - vr.end for vr in items),
        min(max_frame - vr.end for vr in items),
    )


def clamp_delta(
    items: list[VideoRegion], kind: str, delta: int, max_frame: int
) -> int:
    """選択全体を kind の向きへずらせる量へ delta を丸める

    1 つでも許容範囲を外れたら全体をその分で止める。個別にクランプすると
    選択内の相対位置が崩れ、区間の並びが意図せず詰まる。
    """
    if not items:
        return 0
    low, high = _delta_limits(items, kind, max_frame)
    if low > high:
        # 既に末尾を越えている区間が混ざっている場合。動かさない
        return 0
    return max(low, min(delta, high))


def apply_delta(items: list[VideoRegion], kind: str, delta: int) -> None:
    """clamp_delta で丸めた移動量を選択全体へ当てる"""
    for vr in items:
        if kind == MOVE:
            vr.start += delta
            vr.end += delta
        elif kind == START:
            vr.start += delta
        else:
            vr.end += delta
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_selection.py -q`
Expected: PASS（全 20 件）

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline_selection.py tests/test_video_timeline_selection.py
git commit -m "feat(timeline): 選択集合と一括編集の純ロジックを追加する"
```

---

### Task 2: フレーム描き直しでフォーカスを奪わないようにする

タイムラインウィンドウを操作している最中にシークすると、原寸フレームの描き直しで `canvas.setFocus()` が呼ばれ、タイムラインの Delete と Space が効かなくなる。メインウィンドウがアクティブなときだけフォーカスを受け取るよう直す。

**Files:**
- Modify: `mosaic_tool/app.py:834-856`（`_on_frame_fetched`）
- Test: `tests/test_app.py`（`TestTimelineWindowIntegration` へ追記）

**Interfaces:**
- Consumes: なし
- Produces: なし（挙動の修正のみ）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_app.py` の `class TestTimelineWindowIntegration` の末尾に追記する。

```python
    def test_frame_redraw_keeps_focus_when_window_inactive(self, video, monkeypatch):
        # タイムラインウィンドウ操作中にフォーカスを奪うと Delete や Space が
        # 効かなくなるため、メインウィンドウが非アクティブなら受け取らない
        monkeypatch.setattr(type(video), "isActiveWindow", lambda self: False)
        grabbed = []
        monkeypatch.setattr(
            type(video.canvas), "setFocus", lambda self: grabbed.append(True)
        )
        video._timeline.seek(10)
        assert grabbed == []

    def test_frame_redraw_takes_focus_when_window_active(self, video, monkeypatch):
        monkeypatch.setattr(type(video), "isActiveWindow", lambda self: True)
        grabbed = []
        monkeypatch.setattr(
            type(video.canvas), "setFocus", lambda self: grabbed.append(True)
        )
        video._timeline.seek(10)
        assert grabbed == [True]
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_app.py -q -k "keeps_focus_when_window_inactive"`
Expected: FAIL（`assert [True] == []` — 非アクティブでも `setFocus` が呼ばれる）

- [ ] **Step 3: 実装を書く**

`mosaic_tool/app.py` の `_on_frame_fetched` 末尾（現在の 855-856 行）を置き換える。

変更前:
```python
        self.canvas.set_image(img, video.regions_at(frame))
        self.canvas.setFocus()
```

変更後:
```python
        self.canvas.set_image(img, video.regions_at(frame))
        # タイムラインウィンドウを操作している最中にフォーカスを奪うと、
        # そちらの Delete や Space が効かなくなる
        if self.isActiveWindow():
            self.canvas.setFocus()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_app.py -q -k "focus_when_window"`
Expected: PASS（2 件）

- [ ] **Step 5: 回帰がないことを確認する**

Run: `python -m pytest tests/test_app.py -q`
Expected: PASS

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/app.py tests/test_app.py
git commit -m "fix(timeline): フレーム描き直しでタイムラインのフォーカスを奪わない"
```

---

### Task 3: タイムラインが選択状態を所有する

`TimelineArea` の `self._selected: Region | None` を `TimelineSelection` へ置き換える。`set_data` から `selected` 引数を外し、`selection_changed` シグナルと外部反映用の `set_selection` を追加する。app 側の同期を「タイムライン → キャンバス」主導に組み替える。この時点では単体選択・単体ドラッグの挙動は変えない。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`TimelineArea.__init__` / `set_data` / `_edge_at` / `_bar_at` 呼び出し元 / `mousePressEvent` / `keyPressEvent` / `_paint_bars`、`TimelineWindow.set_data` とシグナル中継）
- Modify: `mosaic_tool/app.py:116`（キャンバス選択の接続先）、`:994-1024`（`_ensure_timeline_window` / `_update_timeline_window`）
- Test: `tests/test_video_timeline_window.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: Task 1 の `TimelineSelection`
- Produces:
  - `TimelineArea.set_data(regions: list[VideoRegion]) -> None`（`selected` 引数なし）
  - `TimelineArea.set_selection(regions: list[Region]) -> None`
  - `TimelineArea.selection_changed = Signal(list)` — `list[Region]` を載せる
  - `TimelineArea._selection: TimelineSelection`
  - `TimelineWindow.set_data(regions)` / `TimelineWindow.set_selection(regions)` / `TimelineWindow.selection_changed`
  - `MosaicWindow._on_canvas_selection_changed()` / `_on_timeline_selection_changed(regions)`
  - `MosaicWindow._pushed_selection: set[int]` — 直前にキャンバスへ流した `Region` の `id` 集合

キャンバスからの通知は 2 種類を無視する。**空の通知**はシーンの作り直しで必ず起きるため、
タイムラインの選択を巻き込ませない。**自分が流した内容と同じ通知**は跳ね返りで、反映すると
タイムラインの複数選択がキャンバスに映る分だけへ削られてしまう。真偽フラグではなく
「流した内容」を覚えることで、Qt の通知が同期か遅延かに左右されなくなり、かつ
「キャンバス上の 1 つをクリックして選択を絞る」操作は従来どおり通る。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` の既存呼び出しを新しい署名へ直す。`set_data(items, X)` の第 2 引数を落とし、選択が必要な箇所は `set_selection` を使う。

置き換え対象（`sed` ではなく 1 箇所ずつ確認して直す）:

- `TestRows`: `area.set_data([...], None)` → `area.set_data([...])`（3 箇所）
- `TestHit.test_edge_at_selected_bar`:
  ```python
    def test_edge_at_selected_bar(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        y = area._row_top(0) + 5
        hit = area._edge_at(QPointF(area._x(10), y))
        assert hit == (item, "start")
        hit = area._edge_at(QPointF(area._x(21), y))
        assert hit == (item, "end")
  ```
- `TestHit.test_edge_at_none_without_selection` は Task 5 で挙動が変わるため、ここでは選択なしのまま残す（Task 5 で削除する）。
  ```python
    def test_edge_at_none_without_selection(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(10), y)) is None
  ```
- `TestHit.test_bar_at`, `TestClickAndSeek.test_bar_click_emits_region_with_clicked_frame`,
  `TestDelete.test_delete_key_without_selection_does_nothing`: 第 2 引数 `None` を落とす
- `TestDrag` の全 6 テストと `TestDelete.test_delete_key_emits_selected`:
  `area.set_data([item], item.region)` → `area.set_data([item])` + `area.set_selection([item.region])`
- `TestWindow.test_window_relays_area_signals`: `delete_requested` は Task 4 まで `Signal(object)` のままなので変更しない

そのうえで新しいテストクラスを追記する。

```python
class TestSelectionOwnership:
    def test_set_selection_maps_regions_to_intervals(self):
        area = make_area(ppf=2.0)
        a, b = vr(10, 20), vr(30, 40)
        area.set_data([a, b])
        area.set_selection([b.region])
        assert area._selection.items() == [b]

    def test_set_selection_does_not_emit(self):
        # 外部からの反映で emit すると app 側で同期が往復する
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        fired = []
        area.selection_changed.connect(fired.append)
        area.set_selection([item.region])
        assert fired == []

    def test_set_data_prunes_vanished_intervals(self):
        area = make_area(ppf=2.0)
        a, b = vr(10, 20), vr(30, 40)
        area.set_data([a, b])
        area.set_selection([a.region, b.region])
        area.set_data([a])          # b が消えた
        assert area._selection.items() == [a]

    def test_set_data_keeps_selection_of_living_intervals(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        area.set_data([item])       # 同じ内容で再反映
        assert area._selection.items() == [item]

    def test_bar_click_emits_selection(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        fired = []
        area.selection_changed.connect(fired.append)
        press(area, area._x(15), area._row_top(0) + 5)
        assert fired == [[item.region]]
```

`tests/test_app.py` の `TestTimelineWindowIntegration` へ追記する。

```python
    def test_timeline_selection_flows_to_canvas(self, video):
        region = _rect_region()
        video.canvas.add_region(region)
        video.canvas.select_regions([])
        video._timeline_window.selection_changed.emit([region])
        assert video.canvas.selected_regions() == [region]

    def test_timeline_selection_skips_regions_outside_the_frame(self, video):
        # 現在フレームに掛からない範囲はキャンバスへ流さない(表示が無い)
        region = _rect_region()
        video._video.regions.append(VideoRegion(region, 50, 60))
        video._timeline_window.selection_changed.emit([region])
        assert video.canvas.selected_regions() == []

    def test_empty_canvas_selection_does_not_clear_the_timeline(self, video):
        # シーンの作り直しは常に空の選択を通知する。巻き込まれてはいけない
        region = _rect_region()
        video.canvas.add_region(region)
        video._timeline_window.set_selection([region])
        video.canvas.select_regions([])
        assert video._timeline_window._area._selection.items() != []

    def test_canvas_selection_flows_to_the_timeline(self, video):
        region = _rect_region()
        video.canvas.add_region(region)
        video.canvas.select_regions([region])
        items = video._timeline_window._area._selection.items()
        assert [i.region for i in items] == [region]

    def test_selection_sync_does_not_bounce_back(self, video):
        # タイムラインの複数選択が、キャンバス経由で可視分だけへ縮まない
        shown, hidden = _rect_region(), _rect_region()
        video.canvas.add_region(shown)
        video._video.regions.append(VideoRegion(hidden, 50, 60))
        window = video._timeline_window
        window.set_data(video._video.regions)
        window._area._selection.replace(video._video.regions)
        window._area.selection_changed.emit(
            [vr.region for vr in video._video.regions]
        )
        assert len(window._area._selection) == 2
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py tests/test_app.py -q`
Expected: FAIL（`set_data() missing 1 required positional argument` および `AttributeError: 'TimelineArea' object has no attribute 'set_selection'`）

- [ ] **Step 3: `TimelineArea` を書き換える**

`mosaic_tool/video/timeline_window.py` の import に追記する。

```python
from mosaic_tool.video.timeline_selection import TimelineSelection
```

`Region` の import は `set_selection` の型注釈で使い続けるため残す。

シグナル宣言（`TimelineArea` 冒頭）へ 1 行追記する。

```python
    selection_changed = Signal(list)          # 選択が変わった([Region])
```

`__init__` の選択保持を置き換える。

変更前:
```python
        self._selected: Region | None = None
```

変更後:
```python
        self._selection = TimelineSelection()
```

`set_data` を置き換える。

```python
    def set_data(self, regions: list[VideoRegion]) -> None:
        """区間一覧を反映し、行構成を作り直す(消えた区間は選択から落とす)"""
        self._rows = build_rows(regions)
        self._selection.prune(regions)
        self._apply_size()
```

`set_selection` と `_emit_selection` を `set_data` の直後に足す。

```python
    def set_selection(self, regions: list[Region]) -> None:
        """外部(キャンバス)の選択を反映する

        自分が発端ではないため selection_changed は出さない。出すと app 側で
        キャンバスとの同期が往復してしまう。
        """
        targets = {id(r) for r in regions}
        self._selection.replace([
            vr
            for row in self._rows
            for vr in row.items
            if id(vr.region) in targets
        ])
        self.update()

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self._selection.regions())
```

`_edge_at` の先頭 2 行（選択が無ければ即 None）は残したまま、選択判定を集合へ向ける。

変更前:
```python
        if self._selected is None:
            return None
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._rows[row_index].items:
            if vr.region is not self._selected:
                continue
```

変更後:
```python
        if len(self._selection) == 0:
            return None
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._rows[row_index].items:
            if not self._selection.contains(vr):
                continue
```

`mousePressEvent` のバー押下部を、選択を自前で更新するよう直す。

変更前:
```python
        self._drag = ("move", vr)
        self._grab_offset = self._frame_at_raw(pos.x()) - vr.start
        self.region_clicked.emit(vr.region, self._frame_at(pos.x()))
```

変更後:
```python
        self._drag = ("move", vr)
        self._grab_offset = self._frame_at_raw(pos.x()) - vr.start
        self._selection.replace([vr])
        self._emit_selection()
        self.update()
        self.region_clicked.emit(vr.region, self._frame_at(pos.x()))
```

`keyPressEvent` の削除条件を置き換える。

変更前:
```python
        if (
            event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and self._selected is not None
        ):
            self.delete_requested.emit(self._selected)
```

変更後:
```python
        if (
            event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and len(self._selection) > 0
        ):
            self.delete_requested.emit(self._selection.items()[0].region)
```

（一括削除は Task 4 で行う。ここでは既存のシグネチャを保つ。）

`_paint_bars` の選択判定を置き換える。

変更前:
```python
                selected = self._selected is not None and vr.region is self._selected
```

変更後:
```python
                selected = self._selection.contains(vr)
```

- [ ] **Step 4: `TimelineWindow` を書き換える**

シグナル宣言へ追記する。

```python
    selection_changed = Signal(list)
```

`__init__` の中継へ追記する。

```python
        self._area.selection_changed.connect(self.selection_changed)
```

`set_data` を置き換え、`set_selection` を足す。

```python
    def set_data(self, regions: list[VideoRegion]) -> None:
        self._area.set_data(regions)

    def set_selection(self, regions: list[Region]) -> None:
        self._area.set_selection(regions)
```

- [ ] **Step 5: `app.py` を書き換える**

`__init__` の同期ガードを足す（`self._timeline_window: TimelineWindow | None = None` の直後、現在の 144 行付近）。

```python
        # 直前にタイムラインからキャンバスへ流した選択。跳ね返りを見分けるために覚える
        self._pushed_selection: set[int] = set()
```

キャンバス選択の接続先を差し替える（現在の 116 行）。

変更前:
```python
        self.canvas.selection_changed.connect(self._update_timeline_window)
```

変更後:
```python
        self.canvas.selection_changed.connect(self._on_canvas_selection_changed)
```

`_ensure_timeline_window` の接続へ 1 行足す。

```python
            window.selection_changed.connect(self._on_timeline_selection_changed)
```

`_update_timeline_window` を置き換える。

```python
    def _update_timeline_window(self) -> None:
        """全区間をタイムラインウィンドウへ反映する"""
        if self._timeline_window is None or self._video is None:
            return
        self._timeline_window.set_data(self._video.regions)
```

選択同期の 2 つのハンドラを `_update_timeline_window` の直後に足す。

```python
    def _on_canvas_selection_changed(self) -> None:
        """キャンバスの選択をタイムラインへ反映する

        2 種類の通知は無視する。空の通知はシーンの作り直し(フレームの描き直し)で
        必ず起きるため、タイムラインの選択を巻き込ませない。自分が流した内容と
        同じ通知は跳ね返りで、反映するとタイムラインの複数選択がキャンバスに
        映る分だけへ削られてしまう。
        """
        if self._timeline_window is None:
            return
        selected = self.canvas.selected_regions()
        if not selected or {id(r) for r in selected} == self._pushed_selection:
            return
        self._timeline_window.set_selection(selected)

    def _on_timeline_selection_changed(self, regions: list) -> None:
        """タイムラインの選択をキャンバスへ反映する(現在フレームに掛かる分だけ)"""
        video = self._video
        if video is None:
            return
        shown = {id(r) for r in video.regions_at(video.frame)}
        visible = [r for r in regions if id(r) in shown]
        self._pushed_selection = {id(r) for r in visible}
        self.canvas.select_regions(visible)
```

- [ ] **Step 6: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py tests/test_app.py -q`
Expected: PASS

- [ ] **Step 7: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 8: コミット**

```bash
git add mosaic_tool/video/timeline_window.py mosaic_tool/app.py tests/test_video_timeline_window.py tests/test_app.py
git commit -m "refactor(timeline): 選択状態をタイムラインが所有するようにする"
```

---

### Task 4: 一括削除と区間編集通知の整理

`delete_requested` を `Signal(list)` にして選択中すべてを消せるようにし、`interval_edited(Region, int, int)` を `intervals_edited()` へ簡素化する。app 側で区間編集後にキャンバス表示を合わせる。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`delete_requested` / `interval_edited` の宣言と発火、中継）
- Modify: `mosaic_tool/app.py`（`_ensure_timeline_window` の接続、`_on_timeline_interval_edited` → `_on_timeline_intervals_edited`、`_on_timeline_delete`）
- Test: `tests/test_video_timeline_window.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: Task 3 の `TimelineArea._selection`
- Produces:
  - `TimelineArea.delete_requested = Signal(list)` — `list[Region]`
  - `TimelineArea.intervals_edited = Signal()` — 引数なし。「区間が変わった」通知のみ
  - `TimelineWindow` が同名で中継する
  - `MosaicWindow._on_timeline_intervals_edited()` / `_on_timeline_delete(regions: list)`

`interval_edited` を引数なしにする理由: `TimelineArea` は既に `VideoRegion.start` / `.end` を直接書き換えており、受け側の `vr.start, vr.end = start, end` は同じ値を代入し直すだけで実質 no-op になっている。一括編集で対象が複数になるため通知だけに寄せる。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` を直す。

`TestWindow.test_window_relays_area_signals` を新しいシグネチャへ置き換える。

```python
    def test_window_relays_area_signals(self):
        QApplication.instance() or QApplication([])
        window = TimelineWindow()
        fired = []
        window.seek_requested.connect(lambda f: fired.append(("seek", f)))
        window.region_clicked.connect(
            lambda r, f: fired.append(("click", r, f))
        )
        window.delete_requested.connect(lambda rs: fired.append(("delete", rs)))
        window.intervals_edited.connect(lambda: fired.append(("edit",)))
        window.selection_changed.connect(lambda rs: fired.append(("sel", rs)))
        window._area.seek_requested.emit(3)
        window._area.region_clicked.emit(None, 7)
        window._area.delete_requested.emit([])
        window._area.intervals_edited.emit()
        window._area.selection_changed.emit([])
        assert fired == [
            ("seek", 3), ("click", None, 7), ("delete", []), ("edit",),
            ("sel", []),
        ]
```

`TestDrag` の `interval_edited` を使う 2 テストを直す。

```python
    def test_edge_drag_edits_interval(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        fired = []
        area.intervals_edited.connect(lambda: fired.append(True))
        y = area._row_top(0) + 5
        press(area, area._x(21), y)       # 終端をつかむ
        move(area, area._x(31), y)        # 終端を 30 まで伸ばす
        assert fired
        assert (item.start, item.end) == (10, 30)

    def test_move_drag_keeps_length(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        fired = []
        area.intervals_edited.connect(lambda: fired.append(True))
        y = area._row_top(0) + 5
        press(area, area._x(15), y)       # バー中央をつかむ
        move(area, area._x(20), y)        # 右へ 5 フレーム
        assert fired
        assert (item.start, item.end) == (15, 25)
```

`TestDelete.test_delete_key_emits_selected` を一括削除へ置き換え、複数選択のテストを足す。

```python
    def test_delete_key_emits_selected(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        fired = []
        area.delete_requested.connect(fired.append)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Delete,
            Qt.KeyboardModifier.NoModifier,
        )
        area.keyPressEvent(event)
        assert fired == [[item.region]]

    def test_delete_key_emits_every_selected_region_once(self):
        area = make_area(ppf=2.0)
        a, b = vr(10, 20), vr(30, 40)
        area.set_data([a, b])
        area.set_selection([a.region, b.region])
        fired = []
        area.delete_requested.connect(fired.append)
        area.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert fired == [[a.region, b.region]]
```

`tests/test_app.py` の該当 3 テストを直し、一括削除のテストを足す。

```python
    def test_interval_edit_marks_dirty(self, video):
        region = _rect_region()
        video.canvas.add_region(region)      # sync で現在フレームの区間になる
        video._dirty = False
        vr = video._video.find(region)
        vr.start, vr.end = 0, 50             # タイムラインが直接書き換える
        video._timeline_window.intervals_edited.emit()
        assert (vr.start, vr.end) == (0, 50)
        assert video._dirty

    def test_interval_edit_shows_regions_newly_covering_the_frame(self, video):
        # 現在フレーム(0)に掛からない区間を掛かるよう伸ばしたら表示へ現れる
        region = _rect_region()
        video._video.regions.append(VideoRegion(region, 50, 60))
        assert video.canvas.get_regions() == []
        video._video.find(region).start = 0
        video._timeline_window.intervals_edited.emit()
        assert video.canvas.get_regions() == [region]

    def test_delete_from_window_removes_region(self, video):
        region = _rect_region()
        video.canvas.add_region(region)
        video._timeline_window.delete_requested.emit([region])
        assert video._video.find(region) is None
        assert video.canvas.get_regions() == []

    def test_delete_offscreen_region_removes_from_session(self, video):
        # 現在フレーム(0)に掛からない範囲はキャンバスに無くても消せる
        region = _rect_region()
        video._video.regions.append(VideoRegion(region, 50, 60))
        video._timeline_window.delete_requested.emit([region])
        assert video._video.find(region) is None

    def test_delete_removes_every_selected_region(self, video):
        shown, hidden = _rect_region(), _rect_region()
        video.canvas.add_region(shown)
        video._video.regions.append(VideoRegion(hidden, 50, 60))
        video._timeline_window.delete_requested.emit([shown, hidden])
        assert video._video.regions == []
        assert video.canvas.get_regions() == []
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py tests/test_app.py -q`
Expected: FAIL（`AttributeError: 'TimelineWindow' object has no attribute 'intervals_edited'`）

- [ ] **Step 3: `timeline_window.py` を直す**

シグナル宣言を置き換える（`TimelineArea`）。

変更前:
```python
    interval_edited = Signal(object, int, int)  # (Region, 開始, 終了) ドラッグ中に逐次
    region_clicked = Signal(object, int)      # (Region, クリック位置のフレーム)
    delete_requested = Signal(object)         # (Region) 選択中バーの削除要求
```

変更後:
```python
    intervals_edited = Signal()               # 区間が変わった(ドラッグ中に逐次)
    region_clicked = Signal(object, int)      # (Region, クリック位置のフレーム)
    delete_requested = Signal(list)           # ([Region]) 選択中バーの削除要求
```

`mouseMoveEvent` の発火を置き換える。

変更前:
```python
            self.interval_edited.emit(vr.region, vr.start, vr.end)
```

変更後:
```python
            self.intervals_edited.emit()
```

`keyPressEvent` の削除発火を置き換える。

変更前:
```python
            self.delete_requested.emit(self._selection.items()[0].region)
```

変更後:
```python
            self.delete_requested.emit(self._selection.regions())
```

`TimelineWindow` のシグナル宣言と中継を置き換える。

変更前:
```python
    interval_edited = Signal(object, int, int)
```
```python
        self._area.interval_edited.connect(self.interval_edited)
```

変更後:
```python
    intervals_edited = Signal()
```
```python
        self._area.intervals_edited.connect(self.intervals_edited)
```

`delete_requested = Signal(object)` を `Signal(list)` へ直す。

- [ ] **Step 4: `app.py` を直す**

接続を置き換える。

変更前:
```python
            window.interval_edited.connect(self._on_timeline_interval_edited)
```

変更後:
```python
            window.intervals_edited.connect(self._on_timeline_intervals_edited)
```

`_on_timeline_interval_edited` を置き換える。

変更前:
```python
    def _on_timeline_interval_edited(
        self, region: Region, start: int, end: int
    ) -> None:
        """タイムラインの端ドラッグ・平行移動を区間へ反映する"""
        video = self._video
        vr = video.find(region) if video is not None else None
        if vr is None:
            return
        vr.start, vr.end = start, end
        self._dirty = True
```

変更後:
```python
    def _on_timeline_intervals_edited(self) -> None:
        """タイムラインでの区間編集を受けて表示と未保存状態を合わせる

        区間の値はタイムライン側が直接書き換えている。ここでは掛かり具合の
        変化をキャンバスへ映す(掛かる範囲の集合が変わったときだけ作り直される)。
        """
        video = self._video
        if video is None:
            return
        self._dirty = True
        self.canvas.set_playback_regions(video.regions_at(video.frame))
```

`_on_timeline_delete` を置き換える。

```python
    def _on_timeline_delete(self, regions: list) -> None:
        """タイムラインで選択中の範囲をまとめて削除する(区間リストからも外す)"""
        video = self._video
        if video is None:
            return
        targets = [
            vr for vr in video.regions if any(vr.region is r for r in regions)
        ]
        if not targets:
            return
        # キャンバスに出ていれば Undo 可能な削除を通す(出ていなければ何もしない)
        self.canvas.delete_regions([vr.region for vr in targets])
        dead = {id(vr) for vr in targets}
        video.regions = [vr for vr in video.regions if id(vr) not in dead]
        self._dirty = True
        self._update_timeline_window()
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py tests/test_app.py -q`
Expected: PASS

- [ ] **Step 6: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/video/timeline_window.py mosaic_tool/app.py tests/test_video_timeline_window.py tests/test_app.py
git commit -m "feat(timeline): 選択中の区間をまとめて削除できるようにする"
```

---

### Task 5: 端ハンドルを全バーでつかめるようにする

`_edge_at` から「選択中のバーに限る」条件を外し、未選択のバーでも端をつかんで伸び縮みできるようにする。密集した行で 1 つのバーが外れても他のバーの端を探せるよう、判定を `return None` から `continue` へ変える。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`_edge_at`, `mousePressEvent`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 3 の `TimelineArea._selection`, `_emit_selection`
- Produces: `TimelineArea._begin_edit(kind: str, anchor: VideoRegion, pos: QPointF) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`TestHit.test_edge_at_none_without_selection` を削除し（挙動が変わった）、次を `TestHit` へ足す。

```python
    def test_edge_at_works_on_unselected_bar(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])       # 選択していない
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(10), y)) == (item, "start")

    def test_edge_at_finds_the_neighbour_when_the_first_bar_misses(self):
        # 同じ行に並ぶ 2 本のうち、後ろのバーの端も拾える
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(20, 29)
        area.set_data([a, b])
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(20), y)) == (b, "start")

    def test_edge_at_outside_any_bar_is_none(self):
        area = make_area(ppf=2.0)
        area.set_data([vr(10, 20)])
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(60), y)) is None
```

`TestDrag` へ足す。

```python
    def test_unselected_bar_edge_drag_selects_and_resizes(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])       # 選択していない
        y = area._row_top(0) + 5
        press(area, area._x(21), y)
        move(area, area._x(31), y)
        assert (item.start, item.end) == (10, 30)
        assert area._selection.items() == [item]
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q -k "edge_at or unselected_bar_edge"`
Expected: FAIL（未選択では `_edge_at` が `None` を返す）

- [ ] **Step 3: `_edge_at` を書き換える**

```python
    def _edge_at(self, pos: QPointF) -> tuple[VideoRegion, str] | None:
        """バーの端 (±HANDLE_PX) をつかんでいればどちらの端かを返す

        選択の有無は問わない。掴んだ時点でそのバーを選択するため、選択と
        リサイズを 2 手に分けずに済む。
        """
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in reversed(self._rows[row_index].items):
            bar = self._bar_rect(row_index, vr)
            d_start = abs(pos.x() - bar.left())
            d_end = abs(pos.x() - bar.right())
            # 短いバーでは判定幅を細めて、中央の平行移動をつかめる余地を残す
            handle = min(HANDLE_PX, bar.width() / 3)
            if min(d_start, d_end) > handle:
                # 同じ行に並ぶ別のバーの端かもしれないので探し続ける
                continue
            return vr, ("start" if d_start <= d_end else "end")
        return None
```

- [ ] **Step 4: `_begin_edit` を足し、`mousePressEvent` から呼ぶ**

`_max_frame` の直前に足す。

```python
    def _begin_edit(self, kind: str, anchor: VideoRegion, pos: QPointF) -> None:
        """つかんだバーを起点に区間編集のドラッグを始める

        つかんだバーが選択外なら、そのバー 1 つだけを選び直す。選択内なら
        選択全体が対象になる。
        """
        if not self._selection.contains(anchor):
            self._selection.replace([anchor])
            self._emit_selection()
            self.update()
        self._drag = (kind, anchor)
        self._drag_items = self._selection.items()
        self._grab_offset = self._frame_at_raw(pos.x()) - anchor.start
```

`__init__` へ `self._drag_items: list[VideoRegion] = []` を `self._grab_offset = 0` の直後に足す。

`mousePressEvent` の端つかみとバー押下を `_begin_edit` 経由へ置き換える。

変更前:
```python
        edge = self._edge_at(pos)
        if edge is not None:
            self._drag = (edge[1], edge[0])
            return
        vr = self._bar_at(pos)
        if vr is None:
            self._drag = None
            return
        self._drag = ("move", vr)
        self._grab_offset = self._frame_at_raw(pos.x()) - vr.start
        self._selection.replace([vr])
        self._emit_selection()
        self.update()
        self.region_clicked.emit(vr.region, self._frame_at(pos.x()))
```

変更後:
```python
        edge = self._edge_at(pos)
        if edge is not None:
            self._begin_edit(edge[1], edge[0], pos)
            return
        vr = self._bar_at(pos)
        if vr is None:
            self._drag = None
            return
        self._begin_edit("move", vr, pos)
        self.region_clicked.emit(vr.region, self._frame_at(pos.x()))
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q`
Expected: PASS

- [ ] **Step 6: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): 選択していないバーでも端をつかんで区間を伸ばせるようにする"
```

---

### Task 6: 複数選択の一括平行移動とリサイズ

Ctrl / Shift クリックで選択をトグルできるようにし、ドラッグを Task 1 の `clamp_delta` / `apply_delta` 経由の一括編集へ置き換える。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`mousePressEvent`, `mouseMoveEvent`, `mouseReleaseEvent`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 1 の `MOVE` / `START` / `END` / `clamp_delta` / `apply_delta`、Task 5 の `_begin_edit` / `_drag_items`
- Produces: `TimelineArea._desired_delta(kind: str, anchor: VideoRegion, x: float) -> int`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` の先頭のヘルパーに、修飾つき押下を足す。

```python
def press_mod(area, x, y, modifier):
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifier,
    )
    area.mousePressEvent(event)
```

新しいテストクラスを追記する。

```python
class TestMultiSelect:
    def _two_rows(self):
        # ペンと矩形で別の行になる 2 本を用意する
        area = make_area(total=100, ppf=2.0)
        a = vr(10, 20, RegionKind.STROKE)
        b = vr(10, 20)
        area.set_data([a, b])
        return area, a, b

    def test_ctrl_click_adds_to_the_selection(self):
        area, a, b = self._two_rows()
        press(area, area._x(15), area._row_top(0) + 5)
        press_mod(
            area, area._x(15), area._row_top(1) + 5,
            Qt.KeyboardModifier.ControlModifier,
        )
        assert area._selection.items() == [a, b]

    def test_ctrl_click_removes_from_the_selection(self):
        area, a, b = self._two_rows()
        area.set_selection([a.region, b.region])
        press_mod(
            area, area._x(15), area._row_top(1) + 5,
            Qt.KeyboardModifier.ControlModifier,
        )
        assert area._selection.items() == [a]

    def test_ctrl_click_does_not_start_a_drag(self):
        area, a, _ = self._two_rows()
        press_mod(
            area, area._x(15), area._row_top(0) + 5,
            Qt.KeyboardModifier.ControlModifier,
        )
        move(area, area._x(40), area._row_top(0) + 5)
        assert (a.start, a.end) == (10, 20)

    def test_shift_click_also_toggles(self):
        area, a, b = self._two_rows()
        press(area, area._x(15), area._row_top(0) + 5)
        press_mod(
            area, area._x(15), area._row_top(1) + 5,
            Qt.KeyboardModifier.ShiftModifier,
        )
        assert area._selection.items() == [a, b]

    def test_ctrl_click_emits_the_new_selection(self):
        area, a, b = self._two_rows()
        area.set_selection([a.region])
        fired = []
        area.selection_changed.connect(fired.append)
        press_mod(
            area, area._x(15), area._row_top(1) + 5,
            Qt.KeyboardModifier.ControlModifier,
        )
        assert fired == [[a.region, b.region]]


class TestBulkEdit:
    def _selected_pair(self, first=(10, 20), second=(40, 50)):
        area = make_area(total=100, ppf=2.0)
        a = vr(*first, RegionKind.STROKE)
        b = vr(*second)
        area.set_data([a, b])
        area.set_selection([a.region, b.region])
        return area, a, b

    def test_move_shifts_every_selected_interval(self):
        area, a, b = self._selected_pair()
        press(area, area._x(15), area._row_top(0) + 5)
        move(area, area._x(20), area._row_top(0) + 5)   # 右へ 5
        assert (a.start, a.end) == (15, 25)
        assert (b.start, b.end) == (45, 55)

    def test_move_stops_when_one_hits_the_last_frame(self):
        area, a, b = self._selected_pair(first=(10, 20), second=(90, 95))
        press(area, area._x(15), area._row_top(0) + 5)
        move(area, area._x(200), area._row_top(0) + 5)
        # b が 99 に当たるので全体が +4 で止まる
        assert (a.start, a.end) == (14, 24)
        assert (b.start, b.end) == (94, 99)

    def test_move_stops_when_one_hits_frame_zero(self):
        # つかむ位置は端ハンドル(±HANDLE_PX)から離す。近いとリサイズになる
        area, a, b = self._selected_pair(first=(3, 20), second=(40, 50))
        press(area, area._x(12), area._row_top(0) + 5)
        move(area, area._x(0) - 200, area._row_top(0) + 5)
        # a が 0 に当たるので全体が -3 で止まる
        assert (a.start, a.end) == (0, 17)
        assert (b.start, b.end) == (37, 47)

    def test_end_edge_drag_extends_every_selected_interval(self):
        area, a, b = self._selected_pair()
        y = area._row_top(0) + 5
        press(area, area._x(21), y)      # a の終端をつかむ
        move(area, area._x(31), y)       # 終端を 30 まで(+10)
        assert (a.start, a.end) == (10, 30)
        assert (b.start, b.end) == (40, 60)

    def test_start_edge_drag_stops_at_the_shortest_interval(self):
        area, a, b = self._selected_pair(first=(10, 20), second=(40, 43))
        y = area._row_top(0) + 5
        press(area, area._x(10), y)      # a の開始をつかむ
        move(area, area._x(50), y)       # 大きく右へ
        # b の幅が 4 なので +3 で止まる(開始が終了を越えない)
        assert (a.start, a.end) == (13, 20)
        assert (b.start, b.end) == (43, 43)

    def test_drag_emits_intervals_edited(self):
        area, a, _ = self._selected_pair()
        fired = []
        area.intervals_edited.connect(lambda: fired.append(True))
        press(area, area._x(15), area._row_top(0) + 5)
        move(area, area._x(20), area._row_top(0) + 5)
        assert fired

    def test_dragging_an_unselected_bar_moves_only_that_bar(self):
        area, a, b = self._selected_pair()
        c = vr(70, 80)
        area.set_data([a, b, c])
        area.set_selection([a.region, b.region])
        row = next(
            i for i, r in enumerate(area._rows) if any(v is c for v in r.items)
        )
        press(area, area._x(75), area._row_top(row) + 5)
        move(area, area._x(80), area._row_top(row) + 5)
        assert (c.start, c.end) == (75, 85)
        assert (a.start, a.end) == (10, 20)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q -k "MultiSelect or BulkEdit"`
Expected: FAIL（Ctrl クリックが選択を差し替えてしまう / 一括編集にならない）

- [ ] **Step 3: import と `mousePressEvent` を書き換える**

import へ追記する。

```python
from mosaic_tool.video.timeline_selection import (
    END,
    MOVE,
    START,
    TimelineSelection,
    apply_delta,
    clamp_delta,
)
```

`_drag` の種類を表す文字列は `MOVE` / `START` / `END` と `"seek"` に統一する。`_edge_at` の戻り値も定数へ置き換える。

```python
            return vr, (START if d_start <= d_end else END)
```

`mousePressEvent` を置き換える。

```python
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if pos.y() < RULER_H:
            self._drag = ("seek", None)
            self.seek_requested.emit(self._frame_at(pos.x()))
            return
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            # 修飾つきクリックは選択の出入りだけを行い、ドラッグは始めない
            vr = self._bar_at(pos)
            self._drag = None
            if vr is not None:
                self._selection.toggle(vr)
                self._emit_selection()
                self.update()
            return
        edge = self._edge_at(pos)
        if edge is not None:
            self._begin_edit(edge[1], edge[0], pos)
            return
        vr = self._bar_at(pos)
        if vr is None:
            self._drag = None
            return
        self._begin_edit(MOVE, vr, pos)
        self.region_clicked.emit(vr.region, self._frame_at(pos.x()))
```

- [ ] **Step 4: `mouseMoveEvent` を一括編集へ置き換える**

```python
    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        kind, anchor = self._drag
        x = event.position().x()
        if kind == "seek":
            self.seek_requested.emit(self._frame_at(x))
            return
        delta = clamp_delta(
            self._drag_items,
            kind,
            self._desired_delta(kind, anchor, x),
            self._max_frame(),
        )
        if delta == 0:
            return
        apply_delta(self._drag_items, kind, delta)
        self.update()
        self.intervals_edited.emit()
```

`_desired_delta` を `_begin_edit` の直後に足す。

```python
    def _desired_delta(self, kind: str, anchor: VideoRegion, x: float) -> int:
        """つかんだバーの目標位置から、選択全体へ当てたい移動量を出す"""
        frame = self._frame_at_raw(x)
        if kind == MOVE:
            return frame - self._grab_offset - anchor.start
        if kind == START:
            return frame - anchor.start
        # 終了側は「バーの右端」をつかむため、境界の 1 つ手前が終了フレーム
        return frame - 1 - anchor.end
```

`mouseReleaseEvent` を置き換える。

```python
    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self._drag_items = []
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q`
Expected: PASS

- [ ] **Step 6: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): 複数選択した区間をまとめて移動・リサイズできるようにする"
```

---

### Task 7: 矩形選択

空白（ルーラーより下でバーに掛からない位置）からのドラッグで矩形選択を始め、矩形と交差するバーを選択する。修飾キーつきなら既存の選択へ足す。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`__init__`, `mousePressEvent`, `mouseMoveEvent`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 1 の `TimelineSelection`
- Produces:
  - 定数 `RUBBER = "rubber"`（`_drag` の種類）
  - `TimelineArea._begin_rubber(pos: QPointF, additive: bool) -> None`
  - `TimelineArea._rubber_rect() -> QRectF`
  - `TimelineArea._apply_rubber() -> None`
  - `TimelineArea._rubber_origin` / `_rubber_end`（`QPointF`）、`_rubber_base`（`list[VideoRegion]`）

- [ ] **Step 1: 失敗するテストを書く**

```python
class TestRubberBand:
    def _three_bars(self):
        # ペン 1 本 + 矩形 2 本(重なりで 2 行に分かれる)
        area = make_area(total=100, ppf=2.0)
        pen = vr(10, 20, RegionKind.STROKE)
        a = vr(10, 20)
        b = vr(15, 25)
        area.set_data([pen, a, b])
        return area, pen, a, b

    def _bottom(self, area):
        return area._row_top(len(area._rows)) - 1

    def test_drag_on_empty_space_selects_crossing_bars(self):
        area, pen, a, b = self._three_bars()
        press(area, area._x(12), self._bottom(area) + 5)   # 全行より下から
        move(area, area._x(18), RULER_H + 1)               # 上へ向かって囲む
        assert set(map(id, area._selection.items())) == {id(pen), id(a), id(b)}

    def test_rubber_selects_bars_that_only_intersect(self):
        # 完全内包でなく、端が掛かるだけでも選ぶ
        area, pen, a, b = self._three_bars()
        press(area, area._x(24), self._bottom(area) + 5)
        move(area, area._x(30), RULER_H + 1)
        assert area._selection.items() == [b]

    def test_rubber_skips_bars_outside_the_rect(self):
        area, pen, a, b = self._three_bars()
        press(area, area._x(50), self._bottom(area) + 5)
        move(area, area._x(60), RULER_H + 1)
        assert area._selection.items() == []

    def test_rubber_narrowed_to_one_row(self):
        area, pen, a, b = self._three_bars()
        y = area._row_top(0) + ROW_H / 2
        # ペンの行だけを横に払う(バーの無い右側から左へ)
        press(area, area._x(60), y)
        move(area, area._x(12), y)
        assert area._selection.items() == [pen]

    def test_plain_rubber_replaces_the_selection(self):
        area, pen, a, b = self._three_bars()
        area.set_selection([a.region])
        press(area, area._x(60), self._bottom(area) + 5)
        move(area, area._x(70), RULER_H + 1)
        assert area._selection.items() == []

    def test_ctrl_rubber_adds_to_the_selection(self):
        area, pen, a, b = self._three_bars()
        area.set_selection([a.region])
        y = area._row_top(0) + ROW_H / 2
        press_mod(
            area, area._x(60), y, Qt.KeyboardModifier.ControlModifier
        )
        move(area, area._x(12), y)
        assert set(map(id, area._selection.items())) == {id(a), id(pen)}

    def test_click_without_drag_clears_the_selection(self):
        area, pen, a, b = self._three_bars()
        area.set_selection([a.region])
        press(area, area._x(60), self._bottom(area) + 5)
        assert area._selection.items() == []

    def test_rubber_emits_the_selection(self):
        area, pen, a, b = self._three_bars()
        fired = []
        area.selection_changed.connect(fired.append)
        y = area._row_top(0) + ROW_H / 2
        press(area, area._x(60), y)
        move(area, area._x(12), y)
        assert fired[-1] == [pen.region]

    def test_rubber_does_not_move_bars(self):
        area, pen, a, b = self._three_bars()
        press(area, area._x(60), self._bottom(area) + 5)
        move(area, area._x(12), RULER_H + 1)
        assert (pen.start, pen.end) == (10, 20)
```

import へ `ROW_H` を足す。

```python
from mosaic_tool.video.timeline_window import (  # noqa: E402
    LABEL_W,
    ROW_H,
    RULER_H,
    TimelineArea,
    TimelineWindow,
)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q -k RubberBand`
Expected: FAIL（空白ドラッグでは何も起きない）

- [ ] **Step 3: 実装を書く**

モジュール先頭の定数へ足す（`HANDLE_PX` の直後）。

```python
# ラバーバンド(矩形選択)を表す _drag の種類
RUBBER = "rubber"
```

`__init__` へ足す（`self._drag_items` の直後）。

```python
        # ラバーバンドの始点・終点(ウィジェット座標)と、加算開始時の元の選択
        self._rubber_origin = QPointF()
        self._rubber_end = QPointF()
        self._rubber_base: list[VideoRegion] = []
```

`mousePressEvent` の修飾つき分岐と空白分岐を、ラバーバンド開始へつなぐ。

変更前（修飾つき分岐）:
```python
            vr = self._bar_at(pos)
            self._drag = None
            if vr is not None:
                self._selection.toggle(vr)
                self._emit_selection()
                self.update()
            return
```

変更後:
```python
            vr = self._bar_at(pos)
            if vr is not None:
                self._drag = None
                self._selection.toggle(vr)
                self._emit_selection()
                self.update()
                return
            self._begin_rubber(pos, additive=True)
            return
```

変更前（空白分岐）:
```python
        vr = self._bar_at(pos)
        if vr is None:
            self._drag = None
            return
```

変更後:
```python
        vr = self._bar_at(pos)
        if vr is None:
            self._begin_rubber(pos, additive=False)
            return
```

`_begin_edit` の直前に 3 つのメソッドを足す。

```python
    def _begin_rubber(self, pos: QPointF, additive: bool) -> None:
        """空白からのドラッグで矩形選択を始める

        修飾なしなら元の選択を捨てる(空白クリックだけで選択解除になる)。
        """
        self._drag = (RUBBER, None)
        self._rubber_origin = pos
        self._rubber_end = pos
        self._rubber_base = self._selection.items() if additive else []
        if not additive and len(self._selection) > 0:
            self._selection.clear()
            self._emit_selection()
        self.update()

    def _rubber_rect(self) -> QRectF:
        """始点と終点から作る選択矩形

        真横や真下へ払うドラッグでは幅か高さが 0 になる。Qt は空の矩形の交差を
        常に偽と返すため、最低 1px の広がりを持たせて 1 行だけを払う操作を通す。
        """
        rect = QRectF(self._rubber_origin, self._rubber_end).normalized()
        if rect.width() < 1.0:
            rect.setWidth(1.0)
        if rect.height() < 1.0:
            rect.setHeight(1.0)
        return rect

    def _apply_rubber(self) -> None:
        """矩形と交差する区間バーを選択する(加算開始なら元の選択へ足す)"""
        rect = self._rubber_rect()
        hits = [
            vr
            for i, row in enumerate(self._rows)
            for vr in row.items
            if self._bar_rect(i, vr).intersects(rect)
        ]
        self._selection.replace(self._rubber_base + hits)
        self._emit_selection()
```

`mouseMoveEvent` の `"seek"` 分岐の直後へ足す。

```python
        if kind == RUBBER:
            self._rubber_end = event.position()
            self._apply_rubber()
            self.update()
            return
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q`
Expected: PASS

- [ ] **Step 5: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): 矩形選択で複数の区間を選べるようにする"
```

---

### Task 8: バー端の縁取りと選択の強調表示

全バーの左右端に縁取り線を描き、選択中は白で太くする。選択が 1 つ以上あるときだけ非選択バーを沈んだ色にして選択を浮き上がらせる。ラバーバンドの矩形も描く。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（配色定数、`paintEvent`, `_paint_bars`、新規 `_paint_bar_edges` / `_paint_rubber`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 7 の `RUBBER` / `_rubber_rect`
- Produces:
  - 定数 `BAR_EDGE_W = 1`, `SELECTED_EDGE_W = 2`, `MIN_EDGE_BAR_W = 5.0`
  - 配色 `_BAR_EDGE`, `_BAR_DIM`, `_SELECTED_EDGE`, `_RUBBER_FILL`, `_RUBBER_LINE`
  - `TimelineArea._paint_bar_edges(painter, bar: QRectF, selected: bool) -> None`
  - `TimelineArea._paint_rubber(painter) -> None`
  - `_HANDLE_COLOR` は `_SELECTED_EDGE` へ統合して削除する

- [ ] **Step 1: 失敗するテストを書く**

`TestPalette` へ追記する。

```python
    def test_selection_dims_the_others(self):
        from mosaic_tool.video import timeline_window as tw

        # 選択があるとき非選択バーを沈めるため、既定色より暗くする
        assert tw._BAR_DIM.lightness() < tw._BAR_COLOR.lightness()
        assert tw._BAR_DIM.lightness() < 128

    def test_bar_edge_stands_out_from_the_bar(self):
        from mosaic_tool.video import timeline_window as tw

        # 端の縁取りはバー本体より明るくして区間の境目を見せる
        assert tw._BAR_EDGE.lightness() > tw._BAR_COLOR.lightness()

    def test_selected_edge_is_the_brightest(self):
        from mosaic_tool.video import timeline_window as tw

        assert tw._SELECTED_EDGE.lightness() > tw._BAR_EDGE.lightness()

    def test_rubber_fill_is_translucent(self):
        from mosaic_tool.video import timeline_window as tw

        # 塗りが不透明だと下のバーが見えず、何を選んでいるか分からない
        assert tw._RUBBER_FILL.alpha() < 255
```

新しいテストクラスを追記する。描画は `QPixmap` へ実際に流して色を読む。

```python
class TestPaint:
    def _render(self, area):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(area.sizeHint())
        pm.fill()
        area.render(pm)
        return pm.toImage()

    def _bar_center_color(self, area, row_index, frame):
        image = self._render(area)
        x = int(area._x(frame)) + 3
        y = int(area._row_top(row_index) + ROW_H / 2)
        return image.pixelColor(x, y)

    def test_unselected_bar_uses_the_default_color(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        assert self._bar_center_color(area, 0, 12) == tw._BAR_COLOR

    def test_others_are_dimmed_while_something_is_selected(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        pen, rect = vr(10, 20, RegionKind.STROKE), vr(10, 20)
        area.set_data([pen, rect])
        area.set_selection([pen.region])
        rows = [
            i for i, r in enumerate(area._rows) if any(v is rect for v in r.items)
        ]
        assert self._bar_center_color(area, rows[0], 12) == tw._BAR_DIM

    def test_selected_bar_uses_the_selected_color(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        assert self._bar_center_color(area, 0, 14) == tw._SELECTED_COLOR

    def test_bar_edges_are_drawn(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        image = self._render(area)
        y = int(area._row_top(0) + ROW_H / 2)
        assert image.pixelColor(int(area._x(10)), y) == tw._BAR_EDGE

    def test_selected_bar_edges_are_white(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        image = self._render(area)
        y = int(area._row_top(0) + ROW_H / 2)
        assert image.pixelColor(int(area._x(10)), y) == tw._SELECTED_EDGE

    def test_selected_bar_is_outlined(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        image = self._render(area)
        # バーの上辺(中央寄りの x)に白線が乗る
        x = int(area._x(15))
        assert image.pixelColor(x, int(area._row_top(0))) == tw._SELECTED_EDGE

    def test_narrow_bars_skip_the_edges(self):
        from mosaic_tool.video import timeline_window as tw

        # 潰れるほど細いバーは縁取りで埋まってしまうので描かない。
        # _bar_rect が幅を最低 3px へ広げるので、それが縁取りの下限を下回る
        area = make_area(total=100, ppf=0.5)
        area.set_data([vr(10, 11)])
        image = self._render(area)
        y = int(area._row_top(0) + ROW_H / 2)
        assert image.pixelColor(int(area._x(10)), y) == tw._BAR_COLOR

    def test_rubber_band_is_painted_while_dragging(self):
        from mosaic_tool.video import timeline_window as tw

        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        top = area._row_top(0)
        # 高さを持たせて払う(真横のドラッグでは塗りが線になり色を読めない)
        press(area, area._x(60), top + 2)
        move(area, area._x(80), top + ROW_H - 2)
        image = self._render(area)
        # 半透明の塗りが乗って行背景と違う色になる
        assert image.pixelColor(
            int(area._x(71)), int(top + ROW_H / 2)
        ) != tw._ROW_BG
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q -k "Palette or Paint"`
Expected: FAIL（`AttributeError: module 'mosaic_tool.video.timeline_window' has no attribute '_BAR_DIM'`）

- [ ] **Step 3: 配色と定数を足す**

`HANDLE_PX` の周辺へ足す。

```python
# バー端の縁取りの幅 (px)。選択中は太くして掴める位置を示す
BAR_EDGE_W = 1
SELECTED_EDGE_W = 2
# これより細いバーには縁取りを描かない(縁で埋まって区間が見えなくなる)。
# _bar_rect が幅を最低 3px に広げるため、それより大きい値にする
MIN_EDGE_BAR_W = 5.0
```

配色定数を差し替える。`_HANDLE_COLOR` を消し、代わりに次を置く。

変更前:
```python
_HANDLE_COLOR = QColor(0xFF, 0xFF, 0xFF)
```

変更後:
```python
_BAR_EDGE = QColor(0xC3, 0xDB, 0xF5)       # バー端の縁取り(区間の境目を見せる)
_BAR_DIM = QColor(0x44, 0x5A, 0x74)        # 選択があるときの非選択バー
_SELECTED_EDGE = QColor(0xFF, 0xFF, 0xFF)  # 選択中バーの縁取り
_RUBBER_FILL = QColor(0x4D, 0xA3, 0xFF, 0x40)  # 矩形選択の塗り(下が見える半透明)
_RUBBER_LINE = QColor(0xCC, 0xDD, 0xFF)    # 矩形選択の枠線
```

import へ `QPen` を足す。

```python
from PySide6.QtGui import QColor, QPainter, QPen
```

- [ ] **Step 4: 描画を書き換える**

`paintEvent` へラバーバンドを挟む。

```python
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(event.rect())
        painter.fillRect(rect, _BG)
        self._paint_rows(painter, rect)
        self._paint_grid(painter, rect)
        self._paint_bars(painter, rect)
        self._paint_rubber(painter)
        self._paint_ruler(painter, rect)
        self._paint_playhead(painter)
        self._paint_labels(painter)
        painter.end()
```

`_paint_bars` を置き換える。

```python
    def _paint_bars(self, painter: QPainter, rect: QRectF) -> None:
        """可視フレーム範囲に掛かる区間バーを描く

        自動検出はフレームごとの区間で数千個になり得るため、
        描画対象を可視範囲に掛かるバーだけへ絞る。
        """
        left_frame = self._frame_at_raw(rect.left()) - 1
        right_frame = self._frame_at_raw(rect.right()) + 1
        # 選択が 1 つ以上あるときだけ非選択バーを沈め、選択を浮き上がらせる
        dim = len(self._selection) > 0
        painter.setPen(Qt.PenStyle.NoPen)
        for i, row in enumerate(self._rows):
            for vr in row.items:
                if vr.end < left_frame or vr.start > right_frame:
                    continue
                selected = self._selection.contains(vr)
                bar = self._bar_rect(i, vr)
                if selected:
                    painter.setBrush(_SELECTED_COLOR)
                else:
                    painter.setBrush(_BAR_DIM if dim else _BAR_COLOR)
                painter.drawRect(bar)
                self._paint_bar_edges(painter, bar, selected)
                if selected:
                    self._paint_selected_outline(painter, bar)

    def _paint_bar_edges(
        self, painter: QPainter, bar: QRectF, selected: bool
    ) -> None:
        """バーの左右端に縁取り線を描き、区間の境目と掴める位置を示す"""
        if bar.width() < MIN_EDGE_BAR_W:
            return
        width = SELECTED_EDGE_W if selected else BAR_EDGE_W
        painter.setBrush(_SELECTED_EDGE if selected else _BAR_EDGE)
        painter.drawRect(QRectF(bar.left(), bar.top(), width, bar.height()))
        painter.drawRect(
            QRectF(bar.right() - width, bar.top(), width, bar.height())
        )

    def _paint_selected_outline(self, painter: QPainter, bar: QRectF) -> None:
        """選択中バーを白線で囲む

        色差だけでなく形でも分かるようにして、バーが密集した行でも見失わない。
        呼び出し後にペンとブラシを塗り用へ戻す。
        """
        painter.setPen(_SELECTED_EDGE)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # drawRect は右端と下端を線の分だけはみ出すため 1px 内側へ寄せる
        painter.drawRect(bar.adjusted(0, 0, -1, -1))
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_rubber(self, painter: QPainter) -> None:
        """ドラッグ中の矩形選択を描く(半透明の塗りと点線枠)"""
        if self._drag is None or self._drag[0] != RUBBER:
            return
        pen = QPen(_RUBBER_LINE)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(_RUBBER_FILL)
        painter.drawRect(self._rubber_rect())
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q`
Expected: PASS

- [ ] **Step 6: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): 区間バーの端に縁取り線を描き選択を強調する"
```

---

### Task 9: カーソル形状で操作を示す

マウストラッキングを有効にし、端付近ではリサイズ、バー上では手のカーソルに変える。

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`__init__`, `mouseMoveEvent`、新規 `_update_cursor`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 5 の `_edge_at`（全バー対象）
- Produces: `TimelineArea._update_cursor(pos: QPointF) -> None`

- [ ] **Step 1: 失敗するテストを書く**

```python
class TestCursor:
    def test_mouse_tracking_is_enabled(self):
        # ボタンを押していない移動でも形状を切り替えるために必要
        area = make_area()
        assert area.hasMouseTracking()

    def test_edge_shows_the_resize_cursor(self):
        area = make_area(total=100, ppf=4.0)
        item = vr(10, 20)
        area.set_data([item])
        area._update_cursor(QPointF(area._x(10), area._row_top(0) + 5))
        assert area.cursor().shape() == Qt.CursorShape.SizeHorCursor

    def test_bar_body_shows_the_hand_cursor(self):
        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        area._update_cursor(QPointF(area._x(15), area._row_top(0) + 5))
        assert area.cursor().shape() == Qt.CursorShape.OpenHandCursor

    def test_empty_space_resets_the_cursor(self):
        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        area._update_cursor(QPointF(area._x(15), area._row_top(0) + 5))
        area._update_cursor(QPointF(area._x(60), area._row_top(0) + 5))
        assert area.cursor().shape() == Qt.CursorShape.ArrowCursor

    def test_ruler_resets_the_cursor(self):
        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        area._update_cursor(QPointF(area._x(15), area._row_top(0) + 5))
        area._update_cursor(QPointF(area._x(15), RULER_H / 2))
        assert area.cursor().shape() == Qt.CursorShape.ArrowCursor

    def test_move_without_drag_updates_the_cursor(self):
        area = make_area(total=100, ppf=4.0)
        area.set_data([vr(10, 20)])
        move(area, area._x(15), area._row_top(0) + 5)
        assert area.cursor().shape() == Qt.CursorShape.OpenHandCursor
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q -k Cursor`
Expected: FAIL（`assert False`（トラッキング無効）および `AttributeError: '_update_cursor'`）

- [ ] **Step 3: 実装を書く**

`__init__` の `setFocusPolicy` の直後へ足す。

```python
        # ボタンを押していない移動でもカーソル形状を切り替えるため必要
        self.setMouseTracking(True)
```

`mouseMoveEvent` の先頭を置き換える。

変更前:
```python
        if self._drag is None:
            return
```

変更後:
```python
        if self._drag is None:
            self._update_cursor(event.position())
            return
```

`_update_cursor` を `_desired_delta` の直後へ足す。

```python
    def _update_cursor(self, pos: QPointF) -> None:
        """カーソル位置に応じて形状を変え、できる操作を示す"""
        if pos.y() < RULER_H:
            self.unsetCursor()
        elif self._edge_at(pos) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._bar_at(pos) is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -q`
Expected: PASS

- [ ] **Step 5: 全体の回帰を確認する**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 実アプリで触って確かめる**

Run: `just run`
確認手順:
1. 動画ファイルをドロップして動画モードへ入る（タイムラインウィンドウが開く）
2. キャンバスに矩形を描き、タイムラインのバーをクリック → **明るい青のまま留まる**（数百 ms 後に色が戻らない）
3. そのままタイムラインで Delete → 範囲が消える（フォーカスが奪われていない）
4. バーの端にカーソルを寄せる → 左右矢印カーソルになり、そのままドラッグで伸縮する
5. 自動検出を走らせて多数のバーを出し、空白から矩形選択で複数を囲む → 囲んだバーが明るく、他が沈む
6. 選択したうちの 1 本を中央からドラッグ → 選択全体が同じ量だけ動く
7. Delete → 選択した全区間が消える

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): カーソル形状でリサイズと移動を示す"
```

---

## 補足: 既存テストの扱い

`tests/test_video_timeline_window.py` は `set_data(regions, selected)` の 2 引数署名に依存しているため、Task 3 でまとめて新署名へ直す。Task 3 の Step 1 に対象を列挙してある。テストの意図（何を確かめているか）は変えず、選択の与え方だけを `set_selection` へ移す。

`tests/test_app.py` の `test_selection_dropped_outside_interval` は「区間外へ移動したらキャンバスの選択が外れる」ことを確かめている。この挙動は変わらない（キャンバスは現在フレームに掛かる範囲しか持たない）。タイムライン側の選択は残るが、このテストはキャンバスだけを見ているため修正不要。

## スコープ外

`canvas.set_image` が毎シークで Undo スタックを消している点（`canvas.py:485`）は別の問題として扱い、本計画では触らない。
