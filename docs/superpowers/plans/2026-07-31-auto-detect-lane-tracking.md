# 自動検出の区間を対象ごとに同じ行へ並べる 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** タイムラインの自動検出行で、直前フレームの区間と外接矩形が重なる区間を同じ行へ並べ、同一対象のバーがジグザグに散らばらないようにする。

**Architecture:** `place_lanes` に外接矩形リスト（省略可）を渡せるようにし、空きレーン探索の前に「末尾が隣接し IoU 最大のレーン」を優先する。データ構造（`VideoRegion`）は変更せず、`build_rows` が `RegionSource.AUTO` のときだけ矩形を渡す。IoU 計算は `regions.py` の既存実装を公開して再利用する。

**Tech Stack:** Python 3, PySide6 (QRectF), pytest

## Global Constraints

- コードのコメント・docstring・エラーメッセージは日本語で書く（`CLAUDE.md`）
- 区間は両端含み。`end == 次の start` は重なりとして扱う（既存の `_fits` の仕様）
- `place_lanes` の第 3 引数は省略可能にし、省略時は現行と完全に同じ割り当てを返す
- しきい値はハードコードせず `lanes.py` のモジュール定数 `LANE_MATCH_IOU = 0.1` に置く
- 適用対象は `RegionSource.AUTO` のみ。ペン・矩形カテゴリの割り当ては変えない
- 設計書: `docs/superpowers/specs/2026-07-31-auto-detect-lane-tracking-design.md`

## ファイル構成

| ファイル | 責務 | 変更内容 |
| --- | --- | --- |
| `mosaic_tool/regions.py` | 範囲の形状・重複判定 | `_iou` を `bbox_iou` として公開（改名のみ） |
| `mosaic_tool/video/lanes.py` | タイムラインの行構成 | `LANE_MATCH_IOU` 定数、`place_lanes` の `rects` 引数と継続マッチ、`build_rows` から AUTO の矩形を供給 |
| `tests/test_video_lanes.py` | 行構成の検証 | 継続マッチのテストクラスを 2 つ追加 |

`tests/test_regions.py` は変更しない（`_iou` は直接テストされておらず、公開 API の
`drop_duplicate_regions` のシグネチャは変わらないため）。

---

### Task 1: IoU ヘルパーを公開する

**Files:**
- Modify: `mosaic_tool/regions.py:75-84`（`_iou` の定義）, `mosaic_tool/regions.py:100`（呼び出し）
- Test: `tests/test_regions.py`（既存テストが通ることの確認のみ。追加なし）

**Interfaces:**
- Consumes: なし
- Produces: `mosaic_tool.regions.bbox_iou(a: QRectF, b: QRectF) -> float` — 外接矩形どうしの重なり率 (0.0〜1.0)。Task 2 が import する

- [ ] **Step 1: `_iou` を `bbox_iou` へ改名する**

`mosaic_tool/regions.py` の関数定義を書き換える。処理内容は一切変えない。

```python
def bbox_iou(a: QRectF, b: QRectF) -> float:
    """外接矩形どうしの重なり率 (0.0〜1.0)"""
    inter = a.intersected(b)
    if inter.isEmpty():
        return 0.0
    intersection = inter.width() * inter.height()
    union = a.width() * a.height() + b.width() * b.height() - intersection
    if union <= 0:
        return 0.0
    return intersection / union
```

- [ ] **Step 2: 呼び出し側を追随させる**

`drop_duplicate_regions` の中の 1 行を書き換える。

```python
        if any(bbox_iou(rect, other) >= iou for other in bounds):
```

- [ ] **Step 3: 旧名が残っていないことを確認する**

Run: `python -m pytest tests/test_regions.py -q` および `grep -rn "_iou(" mosaic_tool tests`
Expected: テストは全件 PASS。grep の結果に `_iou(` は `bbox_iou(` の一部としてしか現れない（`DUPLICATE_IOU` は定数なので無関係）

- [ ] **Step 4: コミット**

```bash
git add mosaic_tool/regions.py
git commit -m "refactor(regions): 外接矩形の IoU 計算を公開関数にする"
```

---

### Task 2: `place_lanes` に継続マッチを実装する

**Files:**
- Modify: `mosaic_tool/video/lanes.py`（先頭の import と定数、`place_lanes` 全体）
- Test: `tests/test_video_lanes.py`（`TestPlaceLanesWithRects` を追加）

**Interfaces:**
- Consumes: `mosaic_tool.regions.bbox_iou(a: QRectF, b: QRectF) -> float`（Task 1）
- Produces: `place_lanes(intervals: list[tuple[int, int]], lanes: list[int | None], rects: list[QRectF] | None = None) -> list[int]` — Task 3 の `build_rows` が第 3 引数付きで呼ぶ

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_lanes.py` の末尾に追加する。ファイル先頭の import に
`from mosaic_tool.video.lanes import ...` があるので、`place_lanes` は既に import 済み。
`QRectF` も先頭で import 済み。

```python
def box(x, y, size=10):
    """位置指定の外接矩形。x が離れているものは重ならない"""
    return QRectF(x, y, size, size)


class TestPlaceLanesWithRects:
    def test_two_tracks_keep_lanes_when_input_order_swaps(self):
        # 同一フレームの 2 対象。次フレームで検出順が入れ替わっても行は保つ
        intervals = [(0, 4), (0, 4), (5, 9), (5, 9)]
        rects = [box(0, 0), box(100, 0), box(101, 0), box(1, 0)]
        assigned = place_lanes(intervals, [None] * 4, rects)
        assert assigned[0] == assigned[3]
        assert assigned[1] == assigned[2]
        assert assigned[0] != assigned[1]

    def test_continues_the_lane_with_the_higher_iou(self):
        # lane0 が (0,0)、lane1 が (100,0)。新区間は lane1 の続きになる
        intervals = [(0, 4), (0, 4), (5, 9)]
        rects = [box(0, 0), box(100, 0), box(100, 0)]
        assert place_lanes(intervals, [None] * 3, rects) == [0, 1, 1]

    def test_no_overlap_falls_back_to_top_lane(self):
        # どのレーンとも重ならない新しい対象は最上段の空きへ
        intervals = [(0, 4), (0, 4), (5, 9)]
        rects = [box(0, 0), box(100, 0), box(200, 0)]
        assert place_lanes(intervals, [None] * 3, rects) == [0, 1, 0]

    def test_gap_breaks_continuation(self):
        # フレーム 5 が空くので隣接せず、継続扱いにしない
        intervals = [(0, 4), (0, 4), (6, 10)]
        rects = [box(0, 0), box(100, 0), box(100, 0)]
        assert place_lanes(intervals, [None] * 3, rects) == [0, 1, 0]

    def test_manual_lane_wins_over_continuation(self):
        # 手動指定は継続マッチより優先される
        intervals = [(0, 4), (0, 4), (5, 9)]
        rects = [box(0, 0), box(100, 0), box(100, 0)]
        assert place_lanes(intervals, [None, None, 0], rects) == [0, 1, 0]

    def test_rects_none_keeps_previous_behaviour(self):
        # 矩形を渡さなければ従来どおり最上段詰め
        intervals = [(0, 4), (0, 4), (5, 9)]
        assert place_lanes(intervals, [None] * 3) == [0, 1, 0]

    def test_many_tracks_stay_separated_fast(self):
        # 自動検出相当: 2 対象 × 1000 フレーム分でも 2 レーンに収まる
        intervals = []
        rects = []
        for f in range(0, 5000, 5):
            intervals += [(f, f + 4), (f, f + 4)]
            rects += [box(0, 0), box(100, 0)]
        assigned = place_lanes(intervals, [None] * len(intervals), rects)
        assert max(assigned) == 1
        assert assigned[0::2] == [assigned[0]] * (len(intervals) // 2)
        assert assigned[1::2] == [assigned[1]] * (len(intervals) // 2)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_video_lanes.py::TestPlaceLanesWithRects -q`
Expected: FAIL。`place_lanes() takes 2 positional arguments but 3 were given` (TypeError)

- [ ] **Step 3: 定数と import を追加する**

`mosaic_tool/video/lanes.py` の import 群と定数を次のようにする。

```python
from bisect import bisect_left, insort
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF

from mosaic_tool.regions import bbox_iou
from mosaic_tool.video.session import RegionSource, VideoRegion

# 直前フレームの区間と同じ行へ続けるとみなす最小の重なり率。外れても最上段詰めへ
# 落ちるだけなので、対象が速く動く場合も拾えるよう緩めに取る
LANE_MATCH_IOU = 0.1
```

- [ ] **Step 4: `place_lanes` を書き換える**

`mosaic_tool/video/lanes.py` の `place_lanes` を丸ごと次に置き換える。
手動レーンの確保（前半）は現行と同じで、末尾の索引更新だけ足している。

```python
def place_lanes(
    intervals: list[tuple[int, int]],
    lanes: list[int | None],
    rects: list[QRectF] | None = None,
) -> list[int]:
    """区間をレーンへ割り当て、入力 index ごとのレーン番号を返す

    lanes[i] が None でなければそのレーンを先に確保する。手動同士が同じ
    レーンで被った場合は、開始フレーム順で後ろに来た方を自動配置へ落とす
    (通常はドラッグ確定時に解消済みで、ここは防御)。

    rects を渡すと、直前フレームで終わっているレーンのうち外接矩形の重なりが
    最大のものへ続けて置く。自動検出は追跡をしないため、同一フレームに複数の
    対象が居ると検出順で行が入れ替わってしまう。それを形状で結び直す。
    残り(新しく現れた対象)は開始フレーム順に最上段の空きレーンへ詰める。
    「最も早く終わったレーン」を選ぶと重ならないチェーンが千鳥状に散らばる
    ため、必ず最上段へ詰める。空きの判定は二分探索なので、自動検出で区間が
    数千個並んでも実用速度を保つ。
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i])
    occupied: list[list[tuple[int, int]]] = []  # レーンごとの占有区間(開始順)
    tails: dict[int, list[tuple[int, QRectF]]] = {}  # 終端フレーム -> (レーン, 矩形)
    assigned: list[int] = [-1] * len(intervals)

    def occupy(i: int, lane: int) -> None:
        """区間 i をレーンへ確定し、継続マッチ用の索引も更新する"""
        insort(occupied[lane], intervals[i])
        assigned[i] = lane
        if rects is not None:
            tails.setdefault(intervals[i][1], []).append((lane, rects[i]))

    for i in order:
        lane = lanes[i]
        if lane is None:
            continue
        while len(occupied) <= lane:
            occupied.append([])
        if _fits(occupied[lane], *intervals[i]):
            occupy(i, lane)

    rest = [i for i in order if assigned[i] < 0]
    for start, group in _group_by_start(intervals, rest):
        if rects is not None:
            _match_tails(intervals, rects, occupied, tails, group, start, occupy)
        for i in group:
            if assigned[i] >= 0:
                continue
            lane = 0
            while lane < len(occupied) and not _fits(occupied[lane], *intervals[i]):
                lane += 1
            if lane == len(occupied):
                occupied.append([])
            occupy(i, lane)
    return assigned
```

- [ ] **Step 5: 補助関数を追加する**

`place_lanes` の直前（`_fits` の下）へ置く。

```python
def _group_by_start(
    intervals: list[tuple[int, int]], order: list[int]
) -> list[tuple[int, list[int]]]:
    """開始フレームが同じ index をまとめる(order は開始フレーム順)

    同一フレームの検出をまとめて 1 対 1 で継続先へ割り当てるために使う。
    """
    groups: list[tuple[int, list[int]]] = []
    for i in order:
        start = intervals[i][0]
        if groups and groups[-1][0] == start:
            groups[-1][1].append(i)
        else:
            groups.append((start, [i]))
    return groups


def _match_tails(
    intervals: list[tuple[int, int]],
    rects: list[QRectF],
    occupied: list[list[tuple[int, int]]],
    tails: dict[int, list[tuple[int, QRectF]]],
    group: list[int],
    start: int,
    occupy: Callable[[int, int], None],
) -> None:
    """直前フレームで終わるレーンへ、重なりの大きい区間から順に続ける

    候補は「そのフレームで生存しているトラック」だけなので、総当たりでも
    区間数に対しては線形に収まる。同点は index とレーン番号で決めて、
    入力順が変わっても結果が揺れないようにする。
    """
    candidates = tails.get(start - 1, [])
    if not candidates:
        return
    pairs = [
        (-iou, i, lane)
        for i in group
        for lane, rect in candidates
        if _fits(occupied[lane], *intervals[i])
        and (iou := bbox_iou(rects[i], rect)) >= LANE_MATCH_IOU
    ]
    used_items: set[int] = set()
    used_lanes: set[int] = set()
    for _, i, lane in sorted(pairs):
        if i in used_items or lane in used_lanes:
            continue
        used_items.add(i)
        used_lanes.add(lane)
        occupy(i, lane)
```

- [ ] **Step 6: テストを実行して通ることを確認する**

Run: `python -m pytest tests/test_video_lanes.py -q`
Expected: 新規 7 件を含め全件 PASS（既存の `TestPackLanes` / `TestPlaceLanes` も無変更で通ること）

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/video/lanes.py tests/test_video_lanes.py
git commit -m "feat(video): 重なりの大きい区間を同じ行へ続けて置けるようにする"
```

---

### Task 3: 自動検出行へ外接矩形を供給する

**Files:**
- Modify: `mosaic_tool/video/lanes.py`（`build_rows`）
- Test: `tests/test_video_lanes.py`（`TestBuildRowsAutoTracking` を追加）

**Interfaces:**
- Consumes: `place_lanes(intervals, lanes, rects=None)`（Task 2）, `Region.image_path()`（既存）
- Produces: なし（`build_rows` のシグネチャは不変）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_lanes.py` の末尾に追加する。`box` は Task 2 で定義済み。

```python
def placed_vr(start, end, x, y, source):
    """位置を指定した矩形 1 個ぶんの VideoRegion"""
    region = Region(kind=RegionKind.RECT, rect=box(x, y))
    return VideoRegion(region, start, end, source=source)


class TestBuildRowsAutoTracking:
    def test_auto_rows_follow_the_box(self):
        # 検出順が入れ替わっても、同じ位置の対象は同じ行に並ぶ
        regions = [
            placed_vr(0, 4, 0, 0, RegionSource.AUTO),
            placed_vr(0, 4, 100, 0, RegionSource.AUTO),
            placed_vr(5, 9, 100, 0, RegionSource.AUTO),
            placed_vr(5, 9, 0, 0, RegionSource.AUTO),
        ]
        rows = build_rows(regions)
        assert [[(v.start, v.end) for v in row.items] for row in rows] == [
            [(0, 4), (5, 9)], [(0, 4), (5, 9)],
        ]
        assert [row.items[1].region.rect.x() for row in rows] == [0.0, 100.0]

    def test_rect_category_keeps_top_packing(self):
        # 手描きカテゴリは形状を見ない(従来どおり最上段詰め)
        regions = [
            placed_vr(0, 4, 0, 0, RegionSource.RECT),
            placed_vr(0, 4, 100, 0, RegionSource.RECT),
            placed_vr(5, 9, 100, 0, RegionSource.RECT),
        ]
        rows = build_rows(regions)
        assert [[(v.start, v.end) for v in row.items] for row in rows] == [
            [(0, 4), (5, 9)], [(0, 4)],
        ]
        assert rows[0].items[1].region.rect.x() == 100.0
        assert rows[1].items[0].region.rect.x() == 100.0
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest tests/test_video_lanes.py::TestBuildRowsAutoTracking -q`
Expected: `test_auto_rows_follow_the_box` が FAIL（行が入れ替わり、2 行目の x が 0.0 になる）。
`test_rect_category_keeps_top_packing` は現時点でも PASS（現行動作の固定用）

- [ ] **Step 3: `build_rows` から矩形を渡す**

`mosaic_tool/video/lanes.py` の `build_rows` を次に置き換える。

```python
def build_rows(regions: list[VideoRegion]) -> list[TimelineLane]:
    """カテゴリ順にレーン詰めした行リストを作る(空カテゴリは行を作らない)

    手動で行を指定した区間はその行を優先して確保する。指定によって上の行が
    空く場合も、行番号と表示行がずれないよう空の行を残す。自動検出だけは
    外接矩形も渡し、直前フレームと重なる区間を同じ行へ続ける。
    """
    rows: list[TimelineLane] = []
    for source in CATEGORY_ORDER:
        group = [vr for vr in regions if vr.source is source]
        if not group:
            continue
        rects = None
        if source is RegionSource.AUTO:
            rects = [vr.region.image_path().boundingRect() for vr in group]
        assigned = place_lanes(
            [(vr.start, vr.end) for vr in group], [vr.lane for vr in group], rects
        )
        buckets: list[list[VideoRegion]] = [[] for _ in range(max(assigned) + 1)]
        for vr, lane in zip(group, assigned):
            buckets[lane].append(vr)
        rows += [
            TimelineLane(source, sorted(items, key=lambda v: (v.start, v.end)))
            for items in buckets
        ]
    return rows
```

- [ ] **Step 4: テストを実行して通ることを確認する**

Run: `python -m pytest tests/test_video_lanes.py -q`
Expected: 全件 PASS

- [ ] **Step 5: 全テストを実行して退行が無いことを確認する**

Run: `python -m pytest -q`
Expected: 全件 PASS（`tests/test_video_timeline.py`, `tests/test_video_exporter.py` を含む）

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/video/lanes.py tests/test_video_lanes.py
git commit -m "feat(video): 自動検出の区間を対象ごとに同じ行へ並べる"
```

---

### Task 4: 実アプリで見た目を確認する

**Files:**
- 変更なし（動作確認のみ）

**Interfaces:**
- Consumes: Task 3 までの実装
- Produces: なし

- [ ] **Step 1: アプリを起動して動画を開く**

Run: `just run`（または `python -m mosaic_tool`）
動画ファイルを開き、複数人が同時に写る区間を含む範囲で自動検出を実行する。

- [ ] **Step 2: タイムラインの自動検出行を確認する**

Expected:
- 同じ人物のバーが行を跨がず一直線に並ぶ
- 対象が消えて再登場したときだけ行が変わる
- 行数が対象数より大きく増えていない

- [ ] **Step 3: 手動の行移動が従来どおり効くことを確認する**

自動検出のバーを縦にドラッグして別の行へ移す。
Expected: 移した行に留まり、その後の区間がそこから続く（手動指定が継続マッチより優先される）

- [ ] **Step 4: 結果を報告する**

期待と違う挙動があれば、`LANE_MATCH_IOU` の値と検出間隔 step を添えて報告する。
問題なければ完了。
