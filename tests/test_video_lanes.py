"""タイムラインのレーン詰めロジックの検証"""
from PySide6.QtCore import QRectF

from mosaic_tool.regions import Region, RegionKind
from mosaic_tool.video.lanes import (
    build_rows,
    clamp_lane_delta,
    pack_lanes,
    place_lanes,
)
from mosaic_tool.video.session import RegionSource, VideoRegion


def vr(start, end, kind=RegionKind.RECT):
    if kind is RegionKind.RECT:
        region = Region(kind=kind, rect=QRectF(0, 0, 10, 10))
    else:
        region = Region(kind=kind, points=[], pen_width=10.0)
    return VideoRegion(region, start, end)


class TestPackLanes:
    def test_empty(self):
        assert pack_lanes([]) == []

    def test_non_overlapping_share_lane(self):
        assert pack_lanes([(0, 5), (6, 10), (11, 20)]) == [[0, 1, 2]]

    def test_overlapping_get_new_lane(self):
        assert pack_lanes([(0, 10), (5, 15)]) == [[0], [1]]

    def test_touching_edges_overlap(self):
        # 両端含みの区間なので end == start は重なりとして扱う
        assert pack_lanes([(0, 5), (5, 10)]) == [[0], [1]]

    def test_lane_reused_after_gap(self):
        # 空いているレーンのうち最上段(lane0)が再利用される
        assert pack_lanes([(0, 10), (5, 8), (20, 30)]) == [[0, 2], [1]]

    def test_chain_stays_on_top_lane(self):
        # 序盤の同時検出でレーンが増えても、その後の重ならないチェーンは
        # 下のレーンへ散らばらず最上段に詰まる(タイムラインの千鳥配置の退行防止)
        intervals = [(f, f + 9) for f in range(0, 30, 10) for _ in range(4)]
        intervals += [(f, f + 9) for f in range(30, 130, 10)]
        lanes = pack_lanes(intervals)
        assert len(lanes) == 4
        # チェーン部分(index 12 以降)はすべて lane0 に載る
        assert [i for i in lanes[0] if i >= 12] == list(range(12, len(intervals)))

    def test_lane_items_sorted_by_start(self):
        # 入力が開始順でなくても、レーン内は開始フレーム順に並ぶ
        assert pack_lanes([(20, 30), (0, 10)]) == [[1, 0]]

    def test_many_disjoint_intervals_fast(self):
        # 自動検出相当: フレームごとの独立区間 5000 個でも 1 レーンに詰まる
        lanes = pack_lanes([(i, i) for i in range(0, 10000, 2)])
        assert len(lanes) == 1
        assert len(lanes[0]) == 5000


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

    def test_lane_beyond_limit_is_pulled_back(self):
        # 単独でレーン数を超えていたら、範囲内へ戻す向きへ丸める
        assert clamp_lane_delta([5], [3], 1) == -2

    def test_returns_zero_when_limits_conflict(self):
        # 上端の区間と、範囲を超えた区間が混ざると動かせる余地が無い
        assert clamp_lane_delta([0, 5], [3, 3], 1) == 0
