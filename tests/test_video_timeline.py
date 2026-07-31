"""タイムライン UI(シーク・検出間隔)の検証"""
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


def test_detect_step_default_is_every_frame():
    bar = make_bar()
    assert bar.detect_step() == 1
