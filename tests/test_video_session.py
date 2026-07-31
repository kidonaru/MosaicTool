"""動画編集の状態(区間つき範囲とキャンバス同期)の検証"""
from pathlib import Path

import pytest
from PySide6.QtCore import QRectF

from mosaic_tool.regions import Region, RegionKind
from mosaic_tool.video.ffmpeg import VideoInfo
from mosaic_tool.video.merge import Interval
from mosaic_tool.video.session import RegionSource, VideoRegion, VideoSession


def make_region():
    return Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))


def make_session():
    info = VideoInfo(640, 480, 30.0, "30/1", 300, 10.0, None)
    return VideoSession(Path("movie.mp4"), info)


class TestSource:
    def test_source_derived_from_kind(self):
        rect = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        stroke = Region(kind=RegionKind.STROKE, points=[], pen_width=10.0)
        poly = Region(kind=RegionKind.POLYGON, points=[])
        assert VideoRegion(rect, 0, 0).source is RegionSource.RECT
        assert VideoRegion(stroke, 0, 0).source is RegionSource.PEN
        assert VideoRegion(poly, 0, 0).source is RegionSource.AUTO

    def test_add_intervals_marks_auto(self):
        session = make_session()
        session.add_intervals([Interval(0, 5, (0.0, 0.0, 10.0, 10.0))])
        assert session.regions[0].source is RegionSource.AUTO

    def test_explicit_source_kept(self):
        rect = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
        vr = VideoRegion(rect, 0, 0, source=RegionSource.AUTO)
        assert vr.source is RegionSource.AUTO


class TestRegionsAt:
    def test_only_covering_regions(self):
        session = make_session()
        r1, r2 = make_region(), make_region()
        session.regions = [VideoRegion(r1, 0, 10), VideoRegion(r2, 20, 30)]
        assert session.regions_at(5) == [r1]
        assert session.regions_at(15) == []
        assert session.regions_at(20) == [r2]


class TestSyncFromCanvas:
    def test_new_region_gets_current_frame_interval(self):
        session = make_session()
        session.frame = 7
        region = make_region()
        session.sync_from_canvas([region])
        assert len(session.regions) == 1
        assert (session.regions[0].start, session.regions[0].end) == (7, 7)

    def test_removed_region_dropped(self):
        session = make_session()
        region = make_region()
        session.regions = [VideoRegion(region, 0, 10)]
        session.frame = 5
        session.sync_from_canvas([])
        assert session.regions == []

    def test_region_outside_frame_kept(self):
        # 表示中フレームに掛からない範囲は、キャンバスに無くても消さない
        session = make_session()
        region = make_region()
        session.regions = [VideoRegion(region, 0, 10)]
        session.frame = 50
        session.sync_from_canvas([])
        assert len(session.regions) == 1

    def test_no_displayed_ids_argument(self):
        # 区間外の範囲は表示しない方式にしたため、表示中 id の受け渡しは不要
        session = make_session()
        with pytest.raises(TypeError):
            session.sync_from_canvas([], displayed_ids=set())

    def test_known_region_not_duplicated(self):
        session = make_session()
        region = make_region()
        session.regions = [VideoRegion(region, 0, 10)]
        session.frame = 5
        session.sync_from_canvas([region])
        assert len(session.regions) == 1


class TestIntervalEdges:
    def test_set_start_and_end(self):
        session = make_session()
        region = make_region()
        session.regions = [VideoRegion(region, 10, 10)]
        assert session.set_end(region, 30)
        assert (session.regions[0].start, session.regions[0].end) == (10, 30)
        assert session.set_start(region, 20)
        assert (session.regions[0].start, session.regions[0].end) == (20, 30)

    def test_edges_swap_when_crossed(self):
        session = make_session()
        region = make_region()
        session.regions = [VideoRegion(region, 10, 20)]
        session.set_start(region, 50)
        assert (session.regions[0].start, session.regions[0].end) == (50, 50)
        session.set_end(region, 5)
        assert (session.regions[0].start, session.regions[0].end) == (5, 5)

    def test_unknown_region_returns_false(self):
        session = make_session()
        assert not session.set_start(make_region(), 0)


class TestAddIntervals:
    def test_adds_rect_regions(self):
        session = make_session()
        added = session.add_intervals(
            [Interval(0, 10, (10.0, 20.0, 110.0, 220.0))]
        )
        assert added == 1
        vr = session.regions[0]
        assert (vr.start, vr.end) == (0, 10)
        assert vr.region.kind == RegionKind.RECT
        assert vr.region.rect == QRectF(10, 20, 100, 200)

    def test_adds_polygon_region(self):
        # 輪郭つきの区間は多角形の範囲になる
        session = make_session()
        poly = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
        added = session.add_intervals(
            [Interval(0, 10, (0.0, 0.0, 100.0, 100.0), poly)]
        )
        assert added == 1
        vr = session.regions[0]
        assert vr.region.kind == RegionKind.POLYGON
        assert vr.region.image_path().boundingRect() == QRectF(0, 0, 100, 100)
