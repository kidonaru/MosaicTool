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


def test_build_request_carries_per_model_confidence():
    line = convert.build_request("a.png", {"m1.pt": 0.25, "m2.pt": 0.4}, "cpu")
    assert line.endswith("\n")
    payload = json.loads(line)
    assert payload == {
        "image": "a.png",
        "models": {"m1.pt": 0.25, "m2.pt": 0.4},
        "device": "cpu",
    }


def test_build_request_keeps_non_ascii_filenames_readable():
    line = convert.build_request("画像.png", {"モデル.pt": 0.3}, "")
    assert "画像.png" in line


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
