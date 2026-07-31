"""タイムライン UI(シーク・区間バー・検出間隔)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.timeline import IntervalStrip, TimelineBar  # noqa: E402


def make_bar():
    QApplication.instance() or QApplication([])
    bar = TimelineBar()
    bar.set_range(100)
    return bar


def make_strip(total=101, width=101):
    QApplication.instance() or QApplication([])
    strip = IntervalStrip()
    strip.resize(width, 14)
    strip.set_data([], None, total)
    return strip


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


class TestStripMapping:
    def test_frame_to_x_roundtrip(self):
        # 幅 101px / 101 フレームなら 1 フレーム = 1px
        strip = make_strip()
        assert strip._x(0) == 0
        assert strip._x(100) == 100
        assert strip._frame_at(50) == 50

    def test_frame_at_clamped(self):
        strip = make_strip()
        assert strip._frame_at(-10) == 0
        assert strip._frame_at(1000) == 100


class TestStripHit:
    def test_hit_edge_of_selected(self):
        strip = make_strip()
        strip.set_data([(20, 60)], 0, 101)
        assert strip._hit_edge(20) == "start"
        assert strip._hit_edge(60) == "end"
        assert strip._hit_edge(40) is None

    def test_hit_edge_none_without_selection(self):
        strip = make_strip()
        strip.set_data([(20, 60)], None, 101)
        assert strip._hit_edge(20) is None

    def test_hit_band_index(self):
        strip = make_strip()
        strip.set_data([(0, 30), (50, 80)], None, 101)
        assert strip._hit_band(10) == 0
        assert strip._hit_band(60) == 1
        assert strip._hit_band(40) is None


class TestIntervalLabel:
    def test_label_shows_selected_interval(self):
        bar = make_bar()
        bar.set_intervals([(10, 20)], 0)
        assert "10" in bar._interval_label.text()
        assert "20" in bar._interval_label.text()

    def test_label_dash_without_selection(self):
        bar = make_bar()
        bar.set_intervals([(10, 20)], None)
        assert bar._interval_label.text() == "区間: -"

    def test_strip_edit_relays_signal_and_label(self):
        bar = make_bar()
        fired = []
        bar.interval_edited.connect(lambda s, e: fired.append((s, e)))
        bar._strip.interval_edited.emit(5, 30)
        assert fired == [(5, 30)]
        assert "5" in bar._interval_label.text()


def test_detect_step_default_is_every_frame():
    bar = make_bar()
    assert bar.detect_step() == 1
