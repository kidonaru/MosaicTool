"""タイムラインウィンドウ(カテゴリ別の行と区間バー)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
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
    if kind is RegionKind.RECT:
        region = Region(kind=kind, rect=QRectF(0, 0, 10, 10))
    else:
        region = Region(kind=kind, points=[], pen_width=10.0)
    return VideoRegion(region, start, end)


def make_area(total=100, ppf=2.0):
    QApplication.instance() or QApplication([])
    area = TimelineArea()
    area.set_total(total)
    area._px_per_frame = ppf
    return area


def press(area, x, y):
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    area.mousePressEvent(event)


def press_mod(area, x, y, modifier):
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifier,
    )
    area.mousePressEvent(event)


def move(area, x, y):
    event = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    area.mouseMoveEvent(event)


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
        area._zoom(2.0)
        assert area._px_per_frame == 20.0
        area._px_per_frame = 0.06
        area._zoom(0.5)
        assert area._px_per_frame == 0.05

    def test_zoom_changes_width(self):
        area = make_area(total=100, ppf=2.0)
        w1 = area.sizeHint().width()
        area._zoom(2.0)
        assert area.sizeHint().width() > w1


class TestPalette:
    def test_all_backgrounds_are_dark(self):
        # 動画編集ツールのタイムラインとして暗い配色に統一する
        from mosaic_tool.video import timeline_window as tw

        for color in (tw._BG, tw._RULER_BG, tw._ROW_BG, tw._LABEL_BG):
            assert color.lightness() < 128

    def test_rows_stand_out_from_the_background(self):
        from mosaic_tool.video import timeline_window as tw

        # 行の帯が余白より明るく、行の境目が見える
        assert tw._ROW_BG.lightness() > tw._BG.lightness()

    def test_label_column_is_distinct_from_the_background(self):
        from mosaic_tool.video import timeline_window as tw

        # ラベル列は地色と同じだと列として見えないため、より暗くする
        assert tw._LABEL_BG.lightness() < tw._BG.lightness()

    def test_scrollbar_is_styled_dark(self):
        QApplication.instance() or QApplication([])
        from mosaic_tool.video import timeline_window as tw

        # 明るい既定のスクロールバーが浮かないよう地色を当てる
        window = tw.TimelineWindow()
        assert tw._BG.name() in window._scroll.styleSheet()
        assert "QScrollBar" in window._scroll.styleSheet()

    def test_text_and_bars_are_readable_on_dark(self):
        from mosaic_tool.video import timeline_window as tw

        for color in (tw._TEXT_COLOR, tw._TICK_COLOR, tw._BAR_COLOR):
            assert color.lightness() > 128
        # バーは不透明にして、重なりで濃さが変わらないようにする
        assert tw._BAR_COLOR.alpha() == 255

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


class TestWheel:
    def _wheel(self, area, x, notches, modifiers):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QWheelEvent

        event = QWheelEvent(
            QPointF(x, RULER_H / 2), QPointF(x, RULER_H / 2),
            QPoint(0, 0), QPoint(0, int(120 * notches)),
            Qt.MouseButton.NoButton, modifiers,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        area.wheelEvent(event)

    def test_ctrl_wheel_zooms(self):
        area = make_area(ppf=2.0)
        self._wheel(area, area._x(10), 1, Qt.KeyboardModifier.ControlModifier)
        assert area._px_per_frame > 2.0

    def test_plain_wheel_does_not_zoom(self):
        area = make_area(ppf=2.0)
        self._wheel(area, area._x(10), 1, Qt.KeyboardModifier.NoModifier)
        assert area._px_per_frame == 2.0

    def test_ctrl_wheel_keeps_frame_under_cursor(self):
        area = make_area(ppf=2.0)
        fired = []
        area.scroll_requested.connect(fired.append)
        x = area._x(50)
        self._wheel(area, x, 1, Qt.KeyboardModifier.ControlModifier)
        # カーソル下のフレーム 50 が同じ見かけ位置に残るスクロール量を要求する
        assert fired == [int(area._x(50) - x)]

    def test_plain_wheel_scrolls_horizontally(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, area._x(10), 1, Qt.KeyboardModifier.NoModifier)
        # ホイール上回転で右へ(加算量は正)
        assert fired == [120]

    def test_wheel_down_scrolls_left(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, area._x(10), -1, Qt.KeyboardModifier.NoModifier)
        assert fired == [-120]

    def test_ctrl_wheel_does_not_scroll_horizontally(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, area._x(10), 1, Qt.KeyboardModifier.ControlModifier)
        assert fired == []

    def test_shift_wheel_does_not_scroll_horizontally(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, area._x(10), 1, Qt.KeyboardModifier.ShiftModifier)
        assert fired == []

    def test_window_applies_the_relative_scroll(self):
        window = TimelineWindow()
        window.set_total(10000)
        # スクロールバーの可動域はレイアウト後に決まるため一度表示する
        window.show()
        bar = window._scroll.horizontalScrollBar()
        bar.setValue(300)
        window._area.hscroll_requested.emit(120)
        assert bar.value() == 420


class TestRows:
    def test_set_data_builds_rows(self):
        area = make_area()
        area.set_data([vr(0, 10), vr(5, 15), vr(0, 5, RegionKind.STROKE)])
        # pen 1 行 + rect 2 行(重なりで分割)
        assert [row.source for row in area._rows] == [
            RegionSource.PEN, RegionSource.RECT, RegionSource.RECT,
        ]

    def test_bar_rect_geometry(self):
        area = make_area(ppf=2.0)
        item = vr(10, 19)
        area.set_data([item])
        rect = area._bar_rect(0, item)
        assert rect.left() == LABEL_W + 20
        # 両端含みなので幅は (19 - 10 + 1) * 2px
        assert rect.width() == 20
        assert rect.top() >= RULER_H

    def test_height_follows_row_count(self):
        area = make_area()
        area.set_data([vr(0, 10), vr(5, 15)])
        h2 = area.sizeHint().height()
        area.set_data([vr(0, 10)])
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


class TestHit:
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

    def test_bar_at(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        y = area._row_top(0) + 5
        assert area._bar_at(QPointF(area._x(15), y)) is item
        assert area._bar_at(QPointF(area._x(50), y)) is None

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


class TestDrag:
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

    def test_start_edge_clamped_at_end(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        y = area._row_top(0) + 5
        press(area, area._x(10), y)
        move(area, area._x(50), y)        # 終端より後ろへ
        assert (item.start, item.end) == (20, 20)

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

    def test_move_drag_clamped_at_start(self):
        area = make_area(ppf=2.0)
        item = vr(2, 6)
        area.set_data([item])
        area.set_selection([item.region])
        y = area._row_top(0) + 5
        press(area, area._x(4), y)
        move(area, area._x(0) - 100, y)   # 左端より外へ
        assert (item.start, item.end) == (0, 4)

    def test_move_drag_clamped_at_end(self):
        area = make_area(total=100, ppf=2.0)
        item = vr(90, 94)
        area.set_data([item])
        area.set_selection([item.region])
        y = area._row_top(0) + 5
        press(area, area._x(92), y)
        move(area, area._x(200), y)       # 右端より外へ
        assert (item.start, item.end) == (95, 99)

    def test_unselected_bar_edge_drag_selects_and_resizes(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])       # 選択していない
        y = area._row_top(0) + 5
        press(area, area._x(21), y)
        move(area, area._x(31), y)
        assert (item.start, item.end) == (10, 30)
        assert area._selection.items() == [item]

    def test_release_ends_drag(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        area.set_selection([item.region])
        y = area._row_top(0) + 5
        press(area, area._x(21), y)
        area.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(0, 0),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        move(area, area._x(31), y)       # 離した後の移動は効かない
        assert (item.start, item.end) == (10, 20)


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


class TestClickAndSeek:
    def test_area_has_no_region_clicked_signal(self):
        # バークリックでの自動シークをやめたので、この経路自体を残さない
        assert not hasattr(make_area(ppf=2.0), "region_clicked")

    def test_bar_press_does_not_seek(self):
        # バーを掴んでも再生位置は動かさない(シークはルーラーだけ)
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item])
        fired = []
        area.seek_requested.connect(fired.append)
        press(area, area._x(15), area._row_top(0) + 5)
        assert fired == []
        assert area._selection.items() == [item]

    def test_ruler_press_and_drag_seeks(self):
        area = make_area(ppf=2.0)
        fired = []
        area.seek_requested.connect(fired.append)
        press(area, area._x(30), RULER_H / 2)
        move(area, area._x(40), RULER_H / 2)
        assert fired == [30, 40]


class TestDelete:
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

    def test_delete_key_without_selection_does_nothing(self):
        area = make_area(ppf=2.0)
        area.set_data([vr(10, 20)])
        fired = []
        area.delete_requested.connect(fired.append)
        area.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Backspace,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert fired == []


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


class TestMinorInterval:
    def test_divides_by_ten_when_there_is_room(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        # 主目盛り 100 フレーム、1 フレーム 2px なら 1/10 (10 フレーム = 20px) が入る
        assert _minor_interval(100, 2.0) == 10

    def test_falls_back_to_a_coarser_division(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        # 1/10 では 8px を割るので 1/5 (20 フレーム = 10px) を選ぶ
        assert _minor_interval(100, 0.5) == 20

    def test_returns_the_major_interval_when_it_cannot_divide(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        assert _minor_interval(1, 20.0) == 1

    def test_returns_the_major_interval_when_there_is_no_room(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        assert _minor_interval(100, 0.05) == 100


class TestGridRendering:
    def test_paints_without_error(self):
        # 縦線を含む描画一式が例外なく通ることを確認する(見た目は目視で確認する)
        from PySide6.QtGui import QPixmap

        area = make_area()
        area.set_data([vr(0, 10)])
        area.resize(400, 120)
        pixmap = QPixmap(400, 120)
        area.render(pixmap)

    def test_grid_is_drawn_above_the_row_background(self):
        # 行が無いときは縦線を描かない(下端が RULER_H + ROW_GAP と同じになる)
        from mosaic_tool.video.timeline_window import ROW_GAP

        area = make_area()
        area.set_data([])
        assert area._row_top(0) == RULER_H + ROW_GAP


def test_space_requests_playback_toggle():
    area = make_area()
    fired = []
    area.playback_toggle_requested.connect(lambda: fired.append(True))
    area.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier
        )
    )
    assert fired == [True]


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
