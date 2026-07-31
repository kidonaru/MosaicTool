"""タイムライン UI(シーク・再生操作)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.timeline import TimelineBar  # noqa: E402


def make_bar():
    QApplication.instance() or QApplication([])
    bar = TimelineBar()
    bar.set_range(100)
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

    def test_play_button_text_follows_the_state(self):
        from mosaic_tool.video.timeline import PAUSE_TEXT, PLAY_TEXT

        bar = make_bar()
        assert bar._play_btn.text() == PLAY_TEXT
        bar.set_playing(True)
        assert bar._play_btn.text() == PAUSE_TEXT
        bar.set_playing(False)
        assert bar._play_btn.text() == PLAY_TEXT

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
