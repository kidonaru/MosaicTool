"""タイムライン UI(シーク・再生操作)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.timeline import TimelineBar  # noqa: E402


def make_bar(fps=30.0):
    QApplication.instance() or QApplication([])
    bar = TimelineBar()
    bar.set_range(100, fps)
    return bar


class TestSeek:
    def test_set_frame_without_signal(self):
        bar = make_bar()
        fired = []
        bar.frame_changed.connect(fired.append)
        bar.set_frame(30)
        assert bar.frame() == 30
        assert fired == []

    def test_step_emits_signal(self):
        bar = make_bar()
        fired = []
        bar.frame_changed.connect(fired.append)
        bar.step(1)
        assert fired == [1]

    def test_step_clamped_to_range(self):
        bar = make_bar()
        bar.step(-1)
        assert bar.frame() == 0
        bar.set_frame(99)
        bar.step(5)
        assert bar.frame() == 99

    def test_seek_emits_signal(self):
        # 外部(タイムラインウィンドウ)からのシークはシーク一式を通す
        bar = make_bar()
        fired = []
        bar.frame_changed.connect(fired.append)
        bar.seek(42)
        assert fired == [42]
        assert bar.frame() == 42


class TestTimeLabel:
    def test_label_shows_the_time(self):
        bar = make_bar()
        bar.set_frame(45)
        assert bar._time_label.text().split() == ["00:01.50", "/", "00:03.30"]

    def test_label_without_fps_falls_back_to_zero(self):
        # 動画を開く前は fps が無く、時刻は 00:00.00 のままにする
        QApplication.instance() or QApplication([])
        bar = TimelineBar()
        assert "00:00.00 / 00:00.00" in bar._time_label.text()


def test_interval_api_removed():
    # 区間の表示・編集はタイムラインウィンドウへ移した
    bar = make_bar()
    assert not hasattr(bar, "set_intervals")
    assert not hasattr(bar, "interval_edited")


class TestPlaybackControls:
    def test_detect_step_is_gone(self):
        bar = make_bar()
        assert not hasattr(bar, "detect_step")

    def test_play_button_emits_the_request(self):
        bar = make_bar()
        fired = []
        bar.play_clicked.connect(lambda: fired.append(True))
        bar._play_btn.click()
        assert fired == [True]

    def test_play_button_icon_follows_the_state(self):
        from mosaic_tool.video.timeline import PAUSE_ICON, PLAY_ICON

        bar = make_bar()

        def icon_image(pixmap):
            return bar.style().standardIcon(pixmap).pixmap(16).toImage()

        def shown():
            return bar._play_btn.icon().pixmap(16).toImage()

        assert not bar.is_playing()
        assert shown() == icon_image(PLAY_ICON)
        bar.set_playing(True)
        assert bar.is_playing()
        assert shown() == icon_image(PAUSE_ICON)
        bar.set_playing(False)
        assert not bar.is_playing()
        assert shown() == icon_image(PLAY_ICON)

    def test_play_button_has_no_emoji_text(self):
        # 絵文字字形で描かれて周りのボタンから浮くのを避ける
        assert make_bar()._play_btn.text() == ""

    def test_play_button_does_not_take_focus(self):
        # Space のショートカットとボタンの押下が二重に効かないようにする
        from PySide6.QtCore import Qt

        bar = make_bar()
        assert bar._play_btn.focusPolicy() == Qt.FocusPolicy.NoFocus

    def test_speed_defaults_to_normal(self):
        assert make_bar().speed() == 1.0

    def test_speed_selection_emits_and_is_readable(self):
        bar = make_bar()
        fired = []
        bar.speed_changed.connect(fired.append)
        bar._speed_combo.setCurrentIndex(0)
        assert bar.speed() == 0.25
        assert fired == [0.25]


class TestSeekPreview:
    def _image(self):
        from PySide6.QtGui import QImage

        image = QImage(4, 3, QImage.Format.Format_RGB888)
        image.fill(0)
        return image

    def _hover(self, bar, x):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(x, 5),
            QPointF(x, 5),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(bar._slider, event)

    def test_nearest_thumbnail_is_looked_up(self):
        bar = make_bar()
        for frame in (0, 30, 60):
            bar.add_thumbnail(frame, self._image())
        assert bar._nearest_thumb_frame(40) == 30
        assert bar._nearest_thumb_frame(50) == 60

    def test_no_thumbnails_returns_none(self):
        bar = make_bar()
        assert bar._nearest_thumb_frame(40) is None

    def test_hover_shows_the_preview(self):
        bar = make_bar()
        bar._slider.resize(200, 20)
        bar.add_thumbnail(50, self._image())
        self._hover(bar, 100)
        assert bar._preview.isVisible()
        assert not bar._preview.image.pixmap().isNull()

    def test_preview_caption_shows_the_frame_and_time(self):
        bar = make_bar()
        bar._slider.resize(200, 20)
        bar.add_thumbnail(50, self._image())
        self._hover(bar, 100)
        # 200px の中央 = 50 フレーム目 (30fps なので 00:01.67)
        assert bar._preview.caption.text() == "50  00:01.67"

    def test_hover_without_thumbnails_shows_nothing(self):
        bar = make_bar()
        bar._slider.resize(200, 20)
        self._hover(bar, 100)
        assert not bar._preview.isVisible()

    def test_leave_hides_the_preview(self):
        from PySide6.QtCore import QEvent

        bar = make_bar()
        bar._slider.resize(200, 20)
        bar.add_thumbnail(50, self._image())
        self._hover(bar, 100)
        QApplication.sendEvent(bar._slider, QEvent(QEvent.Type.Leave))
        assert not bar._preview.isVisible()

    def test_clear_thumbnails_hides_the_preview(self):
        bar = make_bar()
        bar._slider.resize(200, 20)
        bar.add_thumbnail(50, self._image())
        self._hover(bar, 100)
        bar.clear_thumbnails()
        assert not bar._preview.isVisible()
        assert bar._nearest_thumb_frame(50) is None
