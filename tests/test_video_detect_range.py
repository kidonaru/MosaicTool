"""検出範囲ダイアログ(タイムコード整形・件数計算・相互クランプ)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.detect_range_dialog import (  # noqa: E402
    DetectRangeDialog,
    detect_frame_count,
    format_timecode,
)


def make_dialog(total=1800, fps=30.0, step=1):
    QApplication.instance() or QApplication([])
    return DetectRangeDialog(total, fps, step)


class TestFormatTimecode:
    def test_zero_frame(self):
        assert format_timecode(0, 30.0) == "00:00.00"

    def test_minutes_and_seconds(self):
        assert format_timecode(1799, 30.0) == "00:59.97"

    def test_over_an_hour(self):
        assert format_timecode(108000, 30.0) == "1:00:00.00"

    def test_zero_fps_is_treated_as_the_head(self):
        # probe で fps が取れないケースは無いが、0 除算で落ちないことを守る
        assert format_timecode(100, 0.0) == "00:00.00"


class TestDetectFrameCount:
    def test_single_frame_range(self):
        assert detect_frame_count(10, 10, 1) == 1

    def test_step_counts_both_ends(self):
        assert detect_frame_count(0, 10, 5) == 3

    def test_step_larger_than_the_range(self):
        assert detect_frame_count(0, 3, 10) == 1

    def test_inverted_range_is_zero(self):
        assert detect_frame_count(10, 5, 1) == 0


class TestDialog:
    def test_defaults_to_the_whole_video(self):
        # 開始は表示中フレームによらず常に先頭 (0)
        assert make_dialog(total=1800).range_result() == (0, 1799, 1)

    def test_keeps_the_previous_step(self):
        assert make_dialog(step=7).range_result()[2] == 7

    def test_start_cannot_exceed_the_end(self):
        dialog = make_dialog(total=1800)
        dialog._end.setValue(100)
        dialog._start.setValue(500)
        assert dialog.range_result()[0] == 100

    def test_end_cannot_go_below_the_start(self):
        dialog = make_dialog(total=1800)
        dialog._start.setValue(200)
        dialog._end.setValue(10)
        assert dialog.range_result()[1] == 200

    def test_count_label_follows_the_values(self):
        dialog = make_dialog(total=1800)
        dialog._end.setValue(100)
        dialog._step.setValue(10)
        assert "11" in dialog._count_label.text()

    def test_time_labels_follow_the_values(self):
        dialog = make_dialog(total=1800, fps=30.0)
        dialog._start.setValue(30)
        assert dialog._start_time.text() == "00:01.00"
