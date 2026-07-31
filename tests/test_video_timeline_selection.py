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
