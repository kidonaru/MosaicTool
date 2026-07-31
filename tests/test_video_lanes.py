"""タイムラインのレーン詰めロジックの検証"""
from PySide6.QtCore import QRectF

from mosaic_tool.regions import Region, RegionKind
from mosaic_tool.video.lanes import build_rows, pack_lanes
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
        # 最も早く終わったレーン(この場合 lane1)が再利用される
        assert pack_lanes([(0, 10), (5, 8), (20, 30)]) == [[0], [1, 2]]

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
