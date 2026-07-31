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
        # ホイール上回転で左へ(加算量は負)
        assert fired == [-120]

    def test_wheel_down_scrolls_right(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, area._x(10), -1, Qt.KeyboardModifier.NoModifier)
        assert fired == [120]

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

    def test_window_relays_area_signals(self):
        QApplication.instance() or QApplication([])
        window = TimelineWindow()
        fired = []
        window.seek_requested.connect(lambda f: fired.append(("seek", f)))
        window.region_clicked.connect(
            lambda r, f: fired.append(("click", r, f))
        )
        window.delete_requested.connect(lambda r: fired.append(("delete", r)))
        window.interval_edited.connect(
            lambda r, s, e: fired.append(("edit", s, e))
        )
        window._area.seek_requested.emit(3)
        window._area.region_clicked.emit(None, 7)
        window._area.delete_requested.emit(None)
        window._area.interval_edited.emit(None, 1, 2)
        assert fired == [
            ("seek", 3), ("click", None, 7), ("delete", None), ("edit", 1, 2),
        ]


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

    def test_edge_at_none_without_selection(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], None)
        y = area._row_top(0) + 5
        assert area._edge_at(QPointF(area._x(10), y)) is None

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

    def test_start_edge_clamped_at_end(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], item.region)
        y = area._row_top(0) + 5
        press(area, area._x(10), y)
        move(area, area._x(50), y)        # 終端より後ろへ
        assert (item.start, item.end) == (20, 20)

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

    def test_move_drag_clamped_at_end(self):
        area = make_area(total=100, ppf=2.0)
        item = vr(90, 94)
        area.set_data([item], item.region)
        y = area._row_top(0) + 5
        press(area, area._x(92), y)
        move(area, area._x(200), y)       # 右端より外へ
        assert (item.start, item.end) == (95, 99)

    def test_release_ends_drag(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], item.region)
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


class TestClickAndSeek:
    def test_bar_click_emits_region_with_clicked_frame(self):
        area = make_area(ppf=2.0)
        item = vr(10, 20)
        area.set_data([item], None)
        fired = []
        area.region_clicked.connect(lambda r, f: fired.append((r, f)))
        press(area, area._x(15), area._row_top(0) + 5)
        assert fired == [(item.region, 15)]

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
        area.set_data([item], item.region)
        fired = []
        area.delete_requested.connect(fired.append)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Delete,
            Qt.KeyboardModifier.NoModifier,
        )
        area.keyPressEvent(event)
        assert fired == [item.region]

    def test_delete_key_without_selection_does_nothing(self):
        area = make_area(ppf=2.0)
        area.set_data([vr(10, 20)], None)
        fired = []
        area.delete_requested.connect(fired.append)
        area.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Backspace,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert fired == []


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
        area.set_data([vr(0, 10)], None)
        area.resize(400, 120)
        pixmap = QPixmap(400, 120)
        area.render(pixmap)

    def test_grid_is_drawn_above_the_row_background(self):
        # 行が無いときは縦線を描かない(下端が RULER_H + ROW_GAP と同じになる)
        from mosaic_tool.video.timeline_window import ROW_GAP

        area = make_area()
        area.set_data([], None)
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
