"""検出結果 JSON → Region 変換の検証(ultralytics も Qt の画面も要らない層)"""
import json

import pytest
from PySide6.QtCore import QPointF

from mosaic_tool.detect import convert
from mosaic_tool.detect.convert import (
    DetectError,
    detections_to_regions,
    thin_points,
)
from mosaic_tool.regions import RegionKind

IMAGE_SIZE = (1000, 1000)


def test_build_request_carries_per_model_confidence_and_classes():
    line = convert.build_request(
        "a.png",
        {
            "m1.pt": {"conf": 0.25, "classes": []},
            "m2.pt": {"conf": 0.4, "classes": ["penis"]},
        },
        "cpu",
    )
    assert line.endswith("\n")
    payload = json.loads(line)
    assert payload == {
        "command": "detect",
        "image": "a.png",
        "models": {
            "m1.pt": {"conf": 0.25, "classes": []},
            "m2.pt": {"conf": 0.4, "classes": ["penis"]},
        },
        "device": "cpu",
    }


def test_build_request_keeps_non_ascii_filenames_readable():
    line = convert.build_request("画像.png", {"モデル.pt": {"conf": 0.3, "classes": []}}, "")
    assert "画像.png" in line
    assert "モデル.pt" in line


def test_build_classes_request_is_a_single_command_line():
    line = convert.build_classes_request()
    assert line.endswith("\n")
    assert json.loads(line) == {"command": "classes"}


def test_parse_response_reports_classes():
    line = json.dumps({"ok": True, "classes": {"m.pt": ["penis", "pussy"]}})
    res = convert.parse_response(line)
    assert res.classes == {"m.pt": ["penis", "pussy"]}
    assert res.detections is None
    assert res.ready is False


def test_parse_response_normalises_class_payloads():
    # 壊れた値が来ても後段が list[str] を前提にできるようにする
    line = json.dumps({"ok": True, "classes": {"m.pt": ["a", 1], "bad.pt": "x"}})
    res = convert.parse_response(line)
    assert res.classes == {"m.pt": ["a", "1"], "bad.pt": []}


def test_parse_response_without_classes_is_still_a_detection():
    res = convert.parse_response(json.dumps({"ok": True, "detections": []}))
    assert res.classes is None
    assert res.detections == []


def test_parse_response_reports_ready():
    res = convert.parse_response(json.dumps({"ok": True, "ready": True}))
    assert res.ready is True
    assert res.detections is None
    assert res.progress is None


def test_parse_response_reports_progress():
    line = json.dumps(
        {"ok": True, "progress": {"done": 1, "total": 3, "model": "m1.pt"}}
    )
    res = convert.parse_response(line)
    assert res.progress == (1, 3, "m1.pt")
    assert res.detections is None


def test_parse_response_reports_detections():
    line = json.dumps({"ok": True, "detections": [{"bbox": [0, 0, 1, 1]}]})
    res = convert.parse_response(line)
    assert res.detections == [{"bbox": [0, 0, 1, 1]}]
    assert res.ready is False


def test_parse_response_treats_missing_detections_as_empty():
    res = convert.parse_response(json.dumps({"ok": True}))
    assert res.detections == []


def test_parse_response_raises_on_error_payload():
    with pytest.raises(DetectError, match="推論に失敗"):
        convert.parse_response(json.dumps({"ok": False, "error": "推論に失敗"}))


def test_parse_response_raises_on_broken_json():
    with pytest.raises(DetectError):
        convert.parse_response("{壊れた")


def test_parse_response_raises_on_non_object_payload():
    with pytest.raises(DetectError):
        convert.parse_response("[1, 2, 3]")


def test_polygon_detection_becomes_polygon_region():
    detections = [{"bbox": [0, 0, 100, 100], "polygon": [[0, 0], [100, 0], [100, 100]]}]
    regions = detections_to_regions(detections, IMAGE_SIZE)
    assert len(regions) == 1
    assert regions[0].kind is RegionKind.POLYGON
    assert regions[0].points[0] == QPointF(0, 0)


def test_detection_without_polygon_falls_back_to_rect():
    regions = detections_to_regions([{"bbox": [10, 20, 110, 70]}], IMAGE_SIZE)
    assert len(regions) == 1
    assert regions[0].kind is RegionKind.RECT
    assert regions[0].rect.width() == 100
    assert regions[0].rect.height() == 50


def test_polygon_with_too_few_points_falls_back_to_rect():
    detections = [{"bbox": [10, 20, 110, 70], "polygon": [[0, 0], [100, 0]]}]
    regions = detections_to_regions(detections, IMAGE_SIZE)
    assert regions[0].kind is RegionKind.RECT


def test_detection_without_bbox_and_polygon_is_skipped():
    regions = detections_to_regions([{"conf": 0.9}, {"bbox": [0, 0, 10, 10]}], IMAGE_SIZE)
    assert len(regions) == 1


def test_dense_contour_points_are_thinned():
    # 1px 刻みの 200 点の輪郭 → 1000x1000 の画像では大幅に間引かれる
    dense = [[float(x), 0.0] for x in range(200)] + [[199.0, 100.0], [0.0, 100.0]]
    regions = detections_to_regions(
        [{"bbox": [0, 0, 199, 100], "polygon": dense}], IMAGE_SIZE
    )
    assert regions[0].kind is RegionKind.POLYGON
    assert len(regions[0].points) < 50


def test_thin_points_keeps_at_least_three_points():
    # 全点が近すぎて 3 点未満になる場合は間引かずに元の点列を返す
    pts = [QPointF(0, 0), QPointF(1, 0), QPointF(0, 1)]
    assert len(thin_points(pts, min_distance=1000)) == 3


def test_regions_are_untransformed():
    # 点列は画像座標そのまま。位置・回転・倍率は初期値
    regions = detections_to_regions(
        [{"bbox": [0, 0, 100, 100], "polygon": [[0, 0], [100, 0], [100, 100]]}], IMAGE_SIZE
    )
    r = regions[0]
    assert r.pos == QPointF(0, 0)
    assert r.rotation == 0.0
    assert (r.scale_x, r.scale_y) == (1.0, 1.0)
