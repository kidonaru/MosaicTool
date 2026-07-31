# タイムラインの行移動と当たり判定の改修 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** タイムラインの区間バーを同カテゴリ内で縦（行）方向にも動かせるようにし、手動で決めた行を自動配置より優先させる。あわせて当たり判定を選択中の区間優先にし、バークリックの自動シークをやめる。

**Architecture:** `VideoRegion` に手動レーン `lane` を持たせ、`lanes.place_lanes` が「手動指定を先に確保 → 残りを最上段の空きへ詰める」順で行を決める。`TimelineArea` は区間リストを保持してドラッグ中に行を組み直し、ドラッグ確定時に掴んでいた区間の行を固定して、被った他区間の手動指定を外す。

**Tech Stack:** Python 3.10 / PySide6 (Qt Widgets) / pytest

## Global Constraints

- コードのコメントとエラーログメッセージは日本語で書く
- 既存の日本語 docstring・コメントのトーンに合わせる（何をするかではなく、なぜそうするかを書く）
- 区間は両端含み（`start` と `end` の両方を含む）。`end == 次の start` は重なりとして扱う
- 縦移動は同じカテゴリ（`RegionSource.PEN` / `RECT` / `AUTO`）内に限る。カテゴリはまたげない
- 手動指定の衝突は「新しい操作を優先し、押し出された側は `lane = None` に戻す」
- ドラッグ終了時（MOVE / START / END のいずれも）の行を手動指定として固定する
- 複数選択の縦移動は全員へ同じ行数を当て、誰か一人でも範囲外へ出る分は全体をそこで止める
- 自動検出はフレームごとの区間が数千個並ぶため、行構成は区間数に対して概ね線形（レーン数を定数とみなして `O(n log n)`）を保つ
- テストは `python -m pytest` で実行する

## File Structure

- `mosaic_tool/video/session.py` — `VideoRegion` に `lane` フィールドを追加（Task 1）
- `mosaic_tool/video/lanes.py` — 手動レーンを尊重した行構成 `place_lanes` と、行移動量のクランプ `clamp_lane_delta`（Task 2, 3）
- `mosaic_tool/video/timeline_window.py` — 当たり判定の優先順、自動シークの削除、縦ドラッグ、行の固定と衝突解決（Task 4〜7）
- `mosaic_tool/app.py` — `region_clicked` の配線とハンドラを削除（Task 5）
- `tests/test_video_session.py`, `tests/test_video_lanes.py`, `tests/test_video_timeline_window.py`, `tests/test_app.py` — 各タスクのテスト

---

### Task 1: `VideoRegion` に手動レーンを持たせる

**Files:**
- Modify: `mosaic_tool/video/session.py`（`VideoRegion` の dataclass 定義）
- Test: `tests/test_video_session.py`

**Interfaces:**
- Consumes: なし
- Produces: `VideoRegion.lane: int | None`（既定 `None`＝自動配置）

- [ ] **Step 1: Write the failing test**

`tests/test_video_session.py` の末尾に追記する。ファイル先頭の import に
`VideoRegion` が無ければ `from mosaic_tool.video.session import VideoRegion` を足す。

```python
class TestVideoRegionLane:
    def test_lane_defaults_to_none(self):
        region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        assert VideoRegion(region, 0, 5).lane is None

    def test_lane_can_be_set(self):
        region = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        assert VideoRegion(region, 0, 5, lane=2).lane == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_session.py::TestVideoRegionLane -v`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument 'lane'`）

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/video/session.py` の `VideoRegion` へフィールドを 1 つ足す。
`source` の後ろに置くこと（既存の位置引数の並びを崩さないため）。

```python
@dataclass
class VideoRegion:
    """モザイク範囲 1 個と適用区間(両端のフレームを含む)"""

    region: Region
    start: int
    end: int
    # タイムラインの行分類。省略時は形状から導出する
    source: RegionSource | None = None
    # タイムラインの行(カテゴリ内のレーン番号)。None は自動配置に任せる
    lane: int | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_session.py -v`
Expected: PASS（既存テストも全て通ること）

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/session.py tests/test_video_session.py
git commit -m "feat(timeline): 区間に手動指定の行を持たせる"
```

---

### Task 2: 手動レーンを尊重した行構成

**Files:**
- Modify: `mosaic_tool/video/lanes.py`
- Test: `tests/test_video_lanes.py`

**Interfaces:**
- Consumes: `VideoRegion.lane`（Task 1）
- Produces:
  - `place_lanes(intervals: list[tuple[int, int]], lanes: list[int | None]) -> list[int]`
    — 入力 index ごとの行番号を返す
  - `pack_lanes(intervals: list[tuple[int, int]]) -> list[list[int]]`
    — 従来通り、行ごとの入力 index リスト（`place_lanes` の上に載せ替える）
  - `build_rows(regions: list[VideoRegion]) -> list[TimelineLane]`
    — 手動レーンを尊重し、必要なら `items` が空の行も返す

- [ ] **Step 1: Write the failing test**

`tests/test_video_lanes.py` に追記する。ファイル先頭の import を
`from mosaic_tool.video.lanes import build_rows, pack_lanes, place_lanes` に変える。

```python
class TestPlaceLanes:
    def test_all_auto_matches_pack_lanes(self):
        # 手動指定が無いときは従来の最上段詰めと同じ結果になる
        intervals = [(0, 10), (5, 8), (20, 30)]
        assert place_lanes(intervals, [None, None, None]) == [0, 1, 0]

    def test_manual_lane_is_kept(self):
        # 単独でも指定した行に置かれる(上の行は空く)
        assert place_lanes([(0, 10)], [2]) == [2]

    def test_auto_avoids_manual_occupancy(self):
        # 手動が lane0 を占めるので、被る自動区間は lane1 へ回る
        assert place_lanes([(0, 10), (5, 15)], [0, None]) == [0, 1]

    def test_auto_fills_lane_above_manual(self):
        # 手動が lane1 を取っても、被らない自動区間は最上段へ詰まる
        assert place_lanes([(0, 10), (20, 30)], [1, None]) == [1, 0]

    def test_manual_conflict_falls_back_to_auto(self):
        # 同じ行で時間が被る手動同士は、後ろにある方が自動配置へ落ちる
        assert place_lanes([(0, 10), (5, 15)], [0, 0]) == [0, 1]

    def test_touching_manual_intervals_conflict(self):
        # 両端含みなので end == 次の start も重なり扱い
        assert place_lanes([(0, 5), (5, 10)], [0, 0]) == [0, 1]

    def test_manual_intervals_share_lane_when_disjoint(self):
        assert place_lanes([(0, 5), (6, 10)], [0, 0]) == [0, 0]


class TestBuildRowsWithLane:
    def test_manual_lane_moves_bar_to_that_row(self):
        a, b = vr(0, 5), vr(20, 30)
        b.lane = 1
        rows = build_rows([a, b])
        assert [[(v.start, v.end) for v in row.items] for row in rows] == [
            [(0, 5)], [(20, 30)],
        ]

    def test_empty_row_kept_above_manual_lane(self):
        # lane2 を指定したら 0 と 1 は空行として残す(行番号と表示行を揃える)
        item = vr(0, 5)
        item.lane = 2
        rows = build_rows([item])
        assert [len(row.items) for row in rows] == [0, 0, 1]
        assert all(row.source is RegionSource.RECT for row in rows)

    def test_manual_lane_pushes_overlapping_auto_down(self):
        a, b = vr(0, 10), vr(5, 15)
        b.lane = 0
        rows = build_rows([a, b])
        # 手動の b が lane0 を取り、被る a が lane1 へ回る
        assert [(v.start, v.end) for v in rows[0].items] == [(5, 15)]
        assert [(v.start, v.end) for v in rows[1].items] == [(0, 10)]

    def test_lane_is_scoped_to_category(self):
        # 行番号はカテゴリごとに数える。矩形の lane1 はペンの行に影響しない
        pen = vr(0, 5, RegionKind.STROKE)
        rect = vr(0, 5, RegionKind.RECT)
        rect.lane = 1
        rows = build_rows([pen, rect])
        assert [row.source for row in rows] == [
            RegionSource.PEN, RegionSource.RECT, RegionSource.RECT,
        ]
        assert [len(row.items) for row in rows] == [1, 0, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_lanes.py -v`
Expected: FAIL（`ImportError: cannot import name 'place_lanes'`）

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/video/lanes.py` の `pack_lanes` を `place_lanes` の上に載せ替え、
`build_rows` を書き換える。`import heapq` は不要になるので消し、
`from bisect import bisect_left, insort` を足す。

```python
def _fits(items: list[tuple[int, int]], start: int, end: int) -> bool:
    """レーンの占有区間(開始順)へ [start, end] を足せるか

    両端含みの区間なので、隣と端が接しただけでも重なりとして弾く。
    """
    i = bisect_left(items, (start, end))
    if i > 0 and items[i - 1][1] >= start:
        return False
    return not (i < len(items) and items[i][0] <= end)


def place_lanes(
    intervals: list[tuple[int, int]], lanes: list[int | None]
) -> list[int]:
    """区間をレーンへ割り当て、入力 index ごとのレーン番号を返す

    lanes[i] が None でなければそのレーンを先に確保する。手動同士が同じ
    レーンで被った場合は、開始フレーム順で後ろに来た方を自動配置へ落とす
    (通常はドラッグ確定時に解消済みで、ここは防御)。残りは開始フレーム順に
    最上段の空きレーンへ詰める。空きの判定は二分探索なので、自動検出で
    区間が数千個並んでも実用速度を保つ。
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i])
    occupied: list[list[tuple[int, int]]] = []  # レーンごとの占有区間(開始順)
    assigned: list[int | None] = [None] * len(intervals)
    for i in order:
        lane = lanes[i]
        if lane is None:
            continue
        while len(occupied) <= lane:
            occupied.append([])
        if _fits(occupied[lane], *intervals[i]):
            insort(occupied[lane], intervals[i])
            assigned[i] = lane
    for i in order:
        if assigned[i] is not None:
            continue
        start, end = intervals[i]
        lane = 0
        while lane < len(occupied) and not _fits(occupied[lane], start, end):
            lane += 1
        if lane == len(occupied):
            occupied.append([])
        insort(occupied[lane], (start, end))
        assigned[i] = lane
    return assigned  # type: ignore[return-value]


def pack_lanes(intervals: list[tuple[int, int]]) -> list[list[int]]:
    """重ならない区間を同じレーンへ詰め、レーンごとの入力 index を返す

    手動指定なしの place_lanes と等価。各レーンの index は開始フレーム順に並ぶ。
    """
    assigned = place_lanes(intervals, [None] * len(intervals))
    lanes: list[list[int]] = [[] for _ in range(max(assigned, default=-1) + 1)]
    for i in sorted(range(len(intervals)), key=lambda i: intervals[i]):
        lanes[assigned[i]].append(i)
    return lanes


def build_rows(regions: list[VideoRegion]) -> list[TimelineLane]:
    """カテゴリ順にレーン詰めした行リストを作る(空カテゴリは行を作らない)

    手動で行を指定した区間はその行を優先して確保する。指定によって上の行が
    空く場合も、行番号と表示行がずれないよう空の行を残す。
    """
    rows: list[TimelineLane] = []
    for source in CATEGORY_ORDER:
        group = [vr for vr in regions if vr.source is source]
        if not group:
            continue
        assigned = place_lanes(
            [(vr.start, vr.end) for vr in group], [vr.lane for vr in group]
        )
        buckets: list[list[VideoRegion]] = [
            [] for _ in range(max(assigned) + 1)
        ]
        for vr, lane in zip(group, assigned):
            buckets[lane].append(vr)
        rows += [
            TimelineLane(source, sorted(items, key=lambda v: (v.start, v.end)))
            for items in buckets
        ]
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_lanes.py tests/test_video_timeline_window.py -v`
Expected: PASS（既存の `TestPackLanes` / `TestBuildRows` も全て通ること。特に
`test_chain_stays_on_top_lane` と `test_many_disjoint_intervals_fast` が
数秒以内に終わることを確認する）

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/lanes.py tests/test_video_lanes.py
git commit -m "feat(timeline): 手動で指定した行を優先して行構成を作る"
```

---

### Task 3: 行移動量のクランプ

**Files:**
- Modify: `mosaic_tool/video/lanes.py`
- Test: `tests/test_video_lanes.py`

**Interfaces:**
- Consumes: なし
- Produces: `clamp_lane_delta(current: list[int], limits: list[int], delta: int) -> int`
  — `current[i]` はカテゴリ内の現在レーン番号、`limits[i]` はそのカテゴリのレーン数
  （末尾 +1 の新規行を許すため、レーン番号の上限は `limits[i]` そのもの）

- [ ] **Step 1: Write the failing test**

`tests/test_video_lanes.py` に追記する。import を
`from mosaic_tool.video.lanes import build_rows, clamp_lane_delta, pack_lanes, place_lanes`
に変える。

```python
class TestClampLaneDelta:
    def test_empty_is_zero(self):
        assert clamp_lane_delta([], [], 3) == 0

    def test_within_range_passes_through(self):
        assert clamp_lane_delta([0], [3], 2) == 2

    def test_clamped_at_top(self):
        assert clamp_lane_delta([1], [3], -5) == -1

    def test_new_row_allowed_at_bottom(self):
        # レーン数 3(番号 0..2)なら、末尾 +1 の 3 まで下がれる
        assert clamp_lane_delta([2], [3], 5) == 1

    def test_group_stops_at_the_first_limit(self):
        # 上端の 0 に居る区間があるので全体が動けない
        assert clamp_lane_delta([0, 2], [3, 3], -1) == 0

    def test_group_keeps_relative_order(self):
        # 下端側の余地に合わせて全体を丸める
        assert clamp_lane_delta([0, 2], [3, 3], 4) == 1

    def test_returns_zero_when_limits_conflict(self):
        # 既にレーン数を超えた区間が混ざっている場合は動かさない
        assert clamp_lane_delta([5], [3], 1) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_lanes.py::TestClampLaneDelta -v`
Expected: FAIL（`ImportError: cannot import name 'clamp_lane_delta'`）

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/video/lanes.py` の末尾に足す。

```python
def clamp_lane_delta(current: list[int], limits: list[int], delta: int) -> int:
    """選択全体を上下へずらせる行数へ delta を丸める

    limits[i] はカテゴリのレーン数。末尾に 1 行足せるようにするため、
    レーン番号の上限は limits[i] そのものになる。個別にクランプすると
    選択内の上下関係が崩れるので、1 つでも外れたら全体をその分で止める。
    """
    if not current:
        return 0
    low = max(-lane for lane in current)
    high = min(limit - lane for lane, limit in zip(current, limits))
    if low > high:
        return 0
    return max(low, min(delta, high))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_lanes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/lanes.py tests/test_video_lanes.py
git commit -m "feat(timeline): 行移動量のクランプを追加する"
```

---

### Task 4: 当たり判定で選択中の区間を優先する

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`_edge_at` / `_bar_at`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: なし
- Produces: `TimelineArea._hit_order(row_index: int) -> list[VideoRegion]`
  — 当たり判定の走査順（選択中を先に、各群の中では後に描いたものを先に）

- [ ] **Step 1: Write the failing test**

`tests/test_video_timeline_window.py` の `class TestHit` の末尾に追記する。

```python
    def test_edge_at_prefers_the_selected_bar(self):
        # 端どうしが接して並ぶ 2 本(同じ行に載る)。選択中の a の終端が
        # b の始端に勝つ
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(10, 19)
        area.set_data([a, b])
        area.set_selection([a.region])
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(10), y)) == (a, "end")

    def test_edge_at_falls_back_to_unselected(self):
        # 選択中のバーが遠ければ、非選択のバーの端を拾う
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(40, 49)
        area.set_data([a, b])
        area.set_selection([a.region])
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(40), y)) == (b, "start")

    def test_bar_at_prefers_the_selected_bar(self):
        # 重なる 2 本は通常は別の行へ分かれるため、走査順だけを見るために
        # 同じ行へ強制的に載せる。選択中の b が勝つ
        area = make_area(ppf=2.0)
        a, b = vr(0, 20), vr(5, 15)
        area.set_data([a, b])
        area._rows[0].items[:] = [a, b]
        area.set_selection([b.region])
        y = area._row_top(0) + 5
        assert area._bar_at(QPointF(area._x(10), y)) is b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_timeline_window.py::TestHit -v`
Expected: FAIL（`test_edge_at_prefers_the_selected_bar` が `(b, 'start')` を返す）

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/video/timeline_window.py` の「ヒット判定」節、`_row_at` の直後に
`_hit_order` を足し、`_edge_at` と `_bar_at` の走査を差し替える。

```python
    def _hit_order(self, row_index: int) -> list[VideoRegion]:
        """当たり判定の走査順

        選択中のバーを先に見る。横に並んだバーの端が判定幅の中で競合しても、
        いま掴もうとしている選択中の端が勝つようにする。各群の中では後に
        描いたものを優先する(見えている方を掴む)。
        """
        items = list(reversed(self._rows[row_index].items))
        selected = [vr for vr in items if self._selection.contains(vr)]
        return selected + [vr for vr in items if not self._selection.contains(vr)]

    def _edge_at(self, pos: QPointF) -> tuple[VideoRegion, str] | None:
        """バーの端 (±HANDLE_PX) をつかんでいればどちらの端かを返す

        選択の有無は問わない。掴んだ時点でそのバーを選択するため、選択と
        リサイズを 2 手に分けずに済む。
        """
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._hit_order(row_index):
            bar = self._bar_rect(row_index, vr)
            d_start = abs(pos.x() - bar.left())
            d_end = abs(pos.x() - bar.right())
            # 短いバーでは判定幅を細めて、中央の平行移動をつかめる余地を残す
            handle = min(HANDLE_PX, bar.width() / 3)
            if min(d_start, d_end) > handle:
                # 同じ行に並ぶ別のバーの端かもしれないので探し続ける
                continue
            return vr, (START if d_start <= d_end else END)
        return None

    def _bar_at(self, pos: QPointF) -> VideoRegion | None:
        """座標に掛かる区間バーを返す(選択中を優先し、次に後に描いたもの)"""
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._hit_order(row_index):
            if self._bar_rect(row_index, vr).contains(pos):
                return vr
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: PASS（既存の `TestHit` / `TestDrag` も全て通ること）

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): 当たり判定で選択中の区間を優先する"
```

---

### Task 5: バークリックの自動シークをやめる

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`region_clicked` の定義・emit・中継を削除）
- Modify: `mosaic_tool/app.py`（`region_clicked` の接続と `_on_timeline_region_clicked` を削除）
- Test: `tests/test_video_timeline_window.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: なし
- Produces: `TimelineArea` / `TimelineWindow` から `region_clicked` シグナルが消える。
  バークリック時のキャンバス選択は既存の `selection_changed` が担う

- [ ] **Step 1: Write the failing test**

まず既存テストを消す。

- `tests/test_video_timeline_window.py` の `TestWindow.test_window_relays_area_signals`
  から `region_clicked` の 3 行（`connect` / `emit` / 期待値の `("click", None, 7)`）を消す。
  結果は次の通りになる。

```python
    def test_window_relays_area_signals(self):
        QApplication.instance() or QApplication([])
        window = TimelineWindow()
        fired = []
        window.seek_requested.connect(lambda f: fired.append(("seek", f)))
        window.delete_requested.connect(lambda rs: fired.append(("delete", rs)))
        window.intervals_edited.connect(lambda: fired.append(("edit",)))
        window.selection_changed.connect(lambda rs: fired.append(("sel", rs)))
        window._area.seek_requested.emit(3)
        window._area.delete_requested.emit([])
        window._area.intervals_edited.emit()
        window._area.selection_changed.emit([])
        assert fired == [("seek", 3), ("delete", []), ("edit",), ("sel", [])]
```

- `tests/test_app.py` の `test_region_click_seeks_to_clicked_frame_and_selects` と
  `test_region_click_without_move_keeps_frame` を削除する。

次に、新しい振る舞いのテストを `tests/test_video_timeline_window.py` の
`class TestDrag` の末尾に追記する。1 つ目はシグナル自体が消えたことを縛る
（これが変更前に落ちるテストになる）。

```python
    def test_area_has_no_region_clicked_signal(self):
        # バークリックでの自動シークをやめたので、この経路自体を残さない
        area = make_area(ppf=2.0)
        assert not hasattr(area, "region_clicked")

    def test_bar_press_does_not_seek(self):
        # バーを掴んでも再生位置は動かさない(シークはルーラーだけ)
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        fired = []
        area.seek_requested.connect(lambda f: fired.append(f))
        press(area, area._x(15), area._row_top(0) + 5)
        assert fired == []
        assert area._selection.items() == [item]

    def test_ruler_press_still_seeks(self):
        area = make_area(ppf=2.0)
        area.set_data([vr(10, 20)])
        fired = []
        area.seek_requested.connect(lambda f: fired.append(f))
        press(area, area._x(15), RULER_H - 2)
        assert fired == [15]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_timeline_window.py::TestDrag -v`
Expected: `test_area_has_no_region_clicked_signal` が FAIL
（`assert not True`）。他の 2 件は PASS する（バー押下自体は
`seek_requested` を出さず、シークは `app.py` の
`_on_timeline_region_clicked` が行っていたため）

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/video/timeline_window.py` から次を消す。

1. `TimelineArea` のシグナル定義
   `region_clicked = Signal(object, int)      # (Region, クリック位置のフレーム)`
2. `mousePressEvent` 末尾の
   `self.region_clicked.emit(vr.region, self._frame_at(pos.x()))`
   （直前の `self._begin_edit(MOVE, vr, pos)` は残す）
3. `TimelineWindow` のシグナル定義 `region_clicked = Signal(object, int)`
4. `TimelineWindow.__init__` の `self._area.region_clicked.connect(self.region_clicked)`

`mousePressEvent` の末尾は次の形になる。

```python
        vr = self._bar_at(pos)
        if vr is None:
            self._begin_rubber(pos, additive=False)
            return
        self._begin_edit(MOVE, vr, pos)
```

`mosaic_tool/app.py` から次を消す。

1. `_ensure_timeline_window` の
   `window.region_clicked.connect(self._on_timeline_region_clicked)`
2. `_on_timeline_region_clicked` メソッド全体

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_timeline_window.py tests/test_app.py -v`
Expected: PASS

未使用の参照が残っていないことも確認する。

Run: `git grep -n region_clicked`
Expected: 何も出力されない（該当なしで終了コード 1）

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/timeline_window.py mosaic_tool/app.py tests/test_video_timeline_window.py tests/test_app.py
git commit -m "feat(timeline): バークリックでの自動シークをやめる"
```

---

### Task 6: 縦ドラッグで行を移動する

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: `place_lanes` / `clamp_lane_delta`（Task 2, 3）、`VideoRegion.lane`（Task 1）
- Produces:
  - `TimelineArea._regions: list[VideoRegion]` — `set_data` で受けた区間の保持
  - `TimelineArea._rebuild_rows() -> None`
  - `TimelineArea._category_span(source) -> tuple[int, int]` — （カテゴリ先頭行 index, 行数）
  - `TimelineArea._row_index_of(vr) -> int` — その区間が載っている行 index
  - `TimelineArea._lane_of(vr) -> int` — カテゴリ内のレーン番号
  - `TimelineArea._claim_lanes(items) -> None` — 被った他区間の `lane` を `None` へ戻す
  - `TimelineArea._apply_lane_drag(y, anchor) -> bool` — 行移動を当てたら True

- [ ] **Step 1: Write the failing test**

`tests/test_video_timeline_window.py` に新しいクラスを追記する。ファイル先頭の
import はそのままでよい（`vr` ヘルパと `press` / `move` を使う）。

```python
class TestLaneDrag:
    def _row_y(self, area, row_index):
        return area._row_top(row_index) + ROW_H / 2

    def test_drag_down_moves_to_next_row(self):
        # 同じ行に並ぶ 2 本。a を 1 行下へ落とす
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(20, 29)
        area.set_data([a, b])
        assert len(area._rows) == 1
        press(area, area._x(5), self._row_y(area, 0))
        move(area, area._x(5), self._row_y(area, 1))
        assert a.lane == 1
        assert area._row_index_of(a) == 1
        # 横位置は変わらない
        assert (a.start, a.end) == (0, 9)

    def test_drag_up_clamped_at_top_row(self):
        area = make_area(ppf=2.0)
        item = vr(0, 9)
        area.set_data([item])
        press(area, area._x(5), self._row_y(area, 0))
        move(area, area._x(5), area._row_top(0) - 100)
        assert item.lane in (None, 0)
        assert area._row_index_of(item) == 0

    def test_drag_cannot_leave_its_category(self):
        # ペン 1 本と矩形 1 本。ペンを下へ払っても矩形の行へは入らない
        area = make_area(ppf=2.0)
        pen = vr(0, 9, RegionKind.STROKE)
        rect = vr(0, 9, RegionKind.RECT)
        area.set_data([pen, rect])
        assert [row.source for row in area._rows] == [
            RegionSource.PEN, RegionSource.RECT,
        ]
        press(area, area._x(5), self._row_y(area, 0))
        move(area, area._x(5), self._row_y(area, 1))
        # ペンのカテゴリ内で末尾 +1 の新規行まで(lane1)しか下がらない
        assert pen.lane == 1
        assert pen.source is RegionSource.PEN
        assert area._rows[area._row_index_of(pen)].source is RegionSource.PEN

    def test_multi_selection_moves_together(self):
        # 同じ行の 2 本をまとめて 1 行下へ
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(20, 29)
        area.set_data([a, b])
        area.set_selection([a.region, b.region])
        press(area, area._x(5), self._row_y(area, 0))
        move(area, area._x(5), self._row_y(area, 1))
        assert (a.lane, b.lane) == (1, 1)

    def test_drag_pushes_the_resident_back_to_auto(self):
        # lane1 に手動で置いた b の位置へ a を落とすと、b が自動配置へ戻る
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(0, 9)
        b.lane = 1
        area.set_data([a, b])
        press(area, area._x(5), self._row_y(area, 0))
        move(area, area._x(5), self._row_y(area, 1))
        assert a.lane == 1
        assert b.lane is None
        assert area._row_index_of(a) == 1
        assert area._row_index_of(b) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_timeline_window.py::TestLaneDrag -v`
Expected: FAIL（`AttributeError: 'TimelineArea' object has no attribute '_row_index_of'`）

- [ ] **Step 3: Write minimal implementation**

3-1. import に `place_lanes` は不要。`build_rows` は既に import 済み。
`clamp_lane_delta` を足す。

```python
from mosaic_tool.video.lanes import (
    CATEGORY_LABELS,
    TimelineLane,
    build_rows,
    clamp_lane_delta,
)
```

3-2. `__init__` に区間の保持を足す（`self._rows` の直後）。

```python
        self._rows: list[TimelineLane] = []
        # ドラッグ中に行を組み直すため、受け取った区間そのものを持つ
        self._regions: list[VideoRegion] = []
```

3-3. `set_data` を書き換え、行の組み直しを 1 か所へまとめる。

```python
    def set_data(self, regions: list[VideoRegion]) -> None:
        """区間一覧を反映し、行構成を作り直す(消えた区間は選択から落とす)"""
        self._regions = list(regions)
        self._selection.prune(regions)
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        """保持している区間から行構成を作り直して描き直す"""
        self._rows = build_rows(self._regions)
        self._apply_size()
```

3-4. 「ヒット判定」節の `_row_at` の後ろへ行の照会を足す。

```python
    def _row_at_nearest(self, y: float) -> int:
        """縦ドラッグ用に、y へ最も近い行 index を返す

        行間の余白でも落とし先が決まるよう丸める。末尾は行数そのものまで許し、
        一番下へ払ったときに新しい行を作れるようにする。
        """
        raw = int((y - RULER_H - ROW_GAP) // (ROW_H + ROW_GAP))
        return max(0, min(len(self._rows), raw))

    def _row_index_of(self, vr: VideoRegion) -> int:
        """その区間が載っている行 index"""
        for i, row in enumerate(self._rows):
            if any(v is vr for v in row.items):
                return i
        return 0

    def _category_span(self, source) -> tuple[int, int]:
        """カテゴリの先頭行 index と行数"""
        rows = [i for i, row in enumerate(self._rows) if row.source is source]
        return (rows[0], len(rows)) if rows else (0, 0)

    def _lane_of(self, vr: VideoRegion) -> int:
        """カテゴリ内のレーン番号(行 index からカテゴリの先頭分を引く)"""
        return self._row_index_of(vr) - self._category_span(vr.source)[0]
```

3-5. 行移動の適用と衝突解決を「マウス・キー操作」節（`_desired_delta` の後）へ足す。

```python
    def _apply_lane_drag(self, y: float, anchor: VideoRegion) -> bool:
        """縦ドラッグを選択全体の行移動へ変える(動かせたら True)"""
        items = self._drag_items
        lanes = [self._lane_of(vr) for vr in items]
        limits = [self._category_span(vr.source)[1] for vr in items]
        start, _ = self._category_span(anchor.source)
        wanted = self._row_at_nearest(y) - start - self._lane_of(anchor)
        delta = clamp_lane_delta(lanes, limits, wanted)
        if delta == 0:
            return False
        for vr, lane in zip(items, lanes):
            vr.lane = lane + delta
        self._claim_lanes(items)
        self._rebuild_rows()
        return True

    def _claim_lanes(self, items: list[VideoRegion]) -> None:
        """掴んでいる区間が取った行から、被る他区間の手動指定を外す

        後から来た操作を優先し、押し出された側は自動配置へ戻す。
        """
        moving = {id(vr) for vr in items}
        for vr in self._regions:
            if id(vr) in moving or vr.lane is None:
                continue
            if any(
                other.source is vr.source
                and other.lane == vr.lane
                and other.start <= vr.end
                and vr.start <= other.end
                for other in items
            ):
                vr.lane = None
```

3-6. `mouseMoveEvent` の MOVE 分岐へ縦方向を足す。既存の横移動の後ろに置き、
どちらかが動いたら再描画と通知を出す形にする。

```python
    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            self._update_cursor(event.position())
            return
        kind, anchor = self._drag
        pos = event.position()
        if kind == "seek":
            self.seek_requested.emit(self._frame_at(pos.x()))
            return
        if kind == RUBBER:
            self._rubber_end = pos
            self._apply_rubber()
            self.update()
            return
        delta = clamp_delta(
            self._drag_items,
            kind,
            self._desired_delta(kind, anchor, pos.x()),
            self._max_frame(),
        )
        moved = delta != 0
        if moved:
            apply_delta(self._drag_items, kind, delta)
        # 平行移動のときだけ行もつかんで動かす(端の伸縮は横方向だけ)
        if kind == MOVE and self._apply_lane_drag(pos.y(), anchor):
            moved = True
        if not moved:
            return
        self.update()
        self.intervals_edited.emit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: PASS（既存の `TestDrag` / `TestMultiSelect` / `TestRows` も全て通ること）

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): 区間を縦にドラッグして行を移動できるようにする"
```

---

### Task 7: ドラッグ終了時に行を固定する

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（`mouseReleaseEvent`）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: `_lane_of` / `_claim_lanes` / `_rebuild_rows`（Task 6）
- Produces: `TimelineArea._fix_lanes(items: list[VideoRegion]) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_video_timeline_window.py` の `class TestLaneDrag` の末尾に追記する。
ファイル冒頭に離すヘルパが無いので、同じクラス内へ足す。

```python
    def _release(self, area):
        area.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(0, 0),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    def test_horizontal_drag_fixes_the_lane(self):
        # 横へ動かしただけでも、手を離した時点の行が手動指定になる
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(20, 29)
        area.set_data([a, b])
        press(area, area._x(5), self._row_y(area, 0))
        move(area, area._x(10), self._row_y(area, 0))
        self._release(area)
        assert a.lane == 0

    def test_edge_drag_fixes_the_lane(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        press(area, area._x(21), self._row_y(area, 0))
        move(area, area._x(31), self._row_y(area, 0))
        self._release(area)
        assert item.lane == 0
        assert (item.start, item.end) == (10, 30)

    def test_fixed_lane_pushes_the_resident_back_to_auto(self):
        # b が手動で lane0。a を b に重なる位置まで伸ばして離すと b が自動へ戻る
        area = make_area(ppf=2.0)
        a, b = vr(0, 9), vr(20, 29)
        b.lane = 0
        area.set_data([a, b])
        press(area, area._x(10), self._row_y(area, 0))   # a の終端をつかむ
        move(area, area._x(26), self._row_y(area, 0))    # b に重なるまで伸ばす
        self._release(area)
        assert a.lane == 0
        assert b.lane is None
        assert area._row_index_of(a) != area._row_index_of(b)

    def test_release_without_drag_items_is_safe(self):
        # 矩形選択やシークで離しても落ちない
        area = make_area(ppf=2.0)
        area.set_data([vr(0, 9)])
        press(area, area._x(50), self._row_y(area, 0))   # 空白から矩形選択
        self._release(area)
        assert area._drag is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_timeline_window.py::TestLaneDrag -v`
Expected: FAIL（`test_horizontal_drag_fixes_the_lane` で `a.lane is None`）

- [ ] **Step 3: Write minimal implementation**

`mosaic_tool/video/timeline_window.py` の `mouseReleaseEvent` を書き換え、
`_fix_lanes` を足す。

```python
    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and self._drag[0] in (MOVE, START, END):
            self._fix_lanes(self._drag_items)
        self._drag = None
        self._drag_items = []

    def _fix_lanes(self, items: list[VideoRegion]) -> None:
        """手を離した時点の行を手動指定として固定する

        横移動やリサイズだけでも固定するのは、操作直後に自動配置でバーが
        別の行へ飛ぶのを防ぐため。押し出された区間は自動配置へ戻す。
        """
        if not items:
            return
        for vr in items:
            vr.lane = self._lane_of(vr)
        self._claim_lanes(items)
        self._rebuild_rows()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v`
Expected: PASS（全テストスイート。特に `tests/test_app.py` と
`tests/test_video_timeline_window.py` に退行が無いこと）

- [ ] **Step 5: Commit**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(timeline): ドラッグ終了時の行を手動指定として固定する"
```

---

## 完了確認

- [ ] `python -m pytest tests/ -q` が全て通る
- [ ] `python -m mosaic_tool` で動画を開き、タイムラインウィンドウで次を手で確認する
  - バーを縦にドラッグして同カテゴリの別の行へ移せる。一番下へ払うと行が増える
  - 別カテゴリの行へは入らない
  - 手動で置いた行に別の区間を落とすと、もとの区間が自動で別の行へ逃げる
  - 横に並んだバーの端が近くても、選択中のバーの端をつかめる
  - バーをクリックしても再生位置が飛ばない。ルーラーのクリックではシークする
