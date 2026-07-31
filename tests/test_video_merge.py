"""検出結果の区間範囲への変換の検証"""
from mosaic_tool.video.merge import (
    Detection,
    Interval,
    merge_detections,
    parse_detection,
)

BOX = (100.0, 100.0, 200.0, 200.0)


def det(frame, bbox=BOX, polygon=None):
    return Detection(frame, bbox, polygon)


def merge(dets, *, step=1, total=1000):
    return merge_detections(dets, step=step, total_frames=total)


class TestMergeDetections:
    def test_detection_becomes_own_interval(self):
        result = merge([det(50)])
        assert result == [Interval(50, 50, BOX)]

    def test_frames_stay_independent(self):
        # 隣接フレームの検出でも連結せず、それぞれ独立した区間になる
        result = merge([det(10), det(11), det(12)])
        assert [(iv.start, iv.end) for iv in result] == [(10, 10), (11, 11), (12, 12)]

    def test_step_extends_to_next_sample(self):
        # 間隔検出では次の検出フレームの直前まで対象が居るとみなす
        result = merge([det(0), det(5)], step=5)
        assert [(iv.start, iv.end) for iv in result] == [(0, 4), (5, 9)]

    def test_end_clamped_to_video(self):
        result = merge([det(8)], step=5, total=10)
        assert result == [Interval(8, 9, BOX)]

    def test_two_objects_same_frame(self):
        other = (500.0, 500.0, 600.0, 600.0)
        result = merge([det(0), det(0, other)])
        assert {iv.bbox for iv in result} == {BOX, other}

    def test_polygon_carried_through(self):
        poly = ((100.0, 100.0), (200.0, 100.0), (200.0, 200.0))
        result = merge([det(0, BOX, poly)])
        assert result == [Interval(0, 0, BOX, poly)]

    def test_empty(self):
        assert merge([]) == []


class TestParseDetection:
    def test_from_bbox(self):
        parsed = parse_detection({"bbox": [1, 2, 3, 4]}, 7)
        assert parsed == Detection(7, (1.0, 2.0, 3.0, 4.0))

    def test_from_polygon(self):
        parsed = parse_detection({"polygon": [[0, 0], [10, 0], [10, 20]]}, 0)
        assert parsed.bbox == (0.0, 0.0, 10.0, 20.0)
        assert parsed.polygon == ((0.0, 0.0), (10.0, 0.0), (10.0, 20.0))

    def test_bbox_and_polygon_keeps_both(self):
        parsed = parse_detection(
            {"bbox": [0, 0, 10, 20], "polygon": [[0, 0], [10, 0], [10, 20]]}, 0
        )
        assert parsed.bbox == (0.0, 0.0, 10.0, 20.0)
        assert parsed.polygon is not None

    def test_none_when_missing(self):
        assert parse_detection({}, 0) is None
        assert parse_detection({"bbox": [1, 2]}, 0) is None
        assert parse_detection({"polygon": [[0, 0]]}, 0) is None
