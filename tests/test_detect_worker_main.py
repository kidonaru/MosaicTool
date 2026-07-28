"""検出ワーカーの検証(ultralytics は入れず、偽のモデルで振る舞いだけ確かめる)"""
from types import SimpleNamespace

from mosaic_tool.detect import worker_main
from mosaic_tool.detect.paths import worker_script_source


class FakeBox:
    def __init__(self, conf, xyxy):
        self.conf = [conf]
        self.xyxy = [SimpleNamespace(tolist=lambda v=xyxy: list(v))]


class FakeResult:
    def __init__(self, boxes, polygons=None):
        self.boxes = boxes
        self.masks = SimpleNamespace(xy=polygons) if polygons is not None else None


class FakeModel:
    """呼ばれたら固定の検出結果を返すモデル"""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def __call__(self, image, conf=None, device=None, verbose=None):
        self.calls.append({"image": image, "conf": conf, "device": device})
        return [self._result]


def test_worker_does_not_import_mosaic_tool():
    # venv 側には mosaic_tool が無いため、依存してはならない
    source = worker_script_source().read_text(encoding="utf-8")
    assert "mosaic_tool" not in source


def test_detect_returns_bbox_and_model_name():
    model = FakeModel(FakeResult([FakeBox(0.9, [1, 2, 3, 4])]))
    result = worker_main.detect([("pussyV2.pt", model)], "img.png", 0.25, "")
    assert result == [
        {"model": "pussyV2.pt", "conf": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]}
    ]


def test_detect_includes_polygon_when_masks_exist():
    model = FakeModel(
        FakeResult([FakeBox(0.9, [0, 0, 10, 10])], polygons=[[(0, 0), (10, 0), (10, 10)]])
    )
    result = worker_main.detect([("m.pt", model)], "img.png", 0.25, "")
    assert result[0]["polygon"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]


def test_detect_combines_multiple_models():
    a = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    b = FakeModel(FakeResult([FakeBox(0.8, [2, 2, 3, 3])]))
    result = worker_main.detect([("a.pt", a), ("b.pt", b)], "img.png", 0.3, "cpu")
    assert [d["model"] for d in result] == ["a.pt", "b.pt"]


def test_detect_passes_conf_and_device_to_model():
    model = FakeModel(FakeResult([]))
    worker_main.detect([("m.pt", model)], "img.png", 0.4, "cpu")
    assert model.calls[0]["conf"] == 0.4
    assert model.calls[0]["device"] == "cpu"


def test_empty_device_is_passed_as_none():
    # 空文字は ultralytics へ渡さず自動選択に任せる
    model = FakeModel(FakeResult([]))
    worker_main.detect([("m.pt", model)], "img.png", 0.4, "")
    assert model.calls[0]["device"] is None


def test_handle_request_returns_error_payload_on_failure():
    class BrokenModel:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("推論に失敗")

    payload = worker_main.handle_request(
        [("m.pt", BrokenModel())], '{"image": "img.png", "conf": 0.25}'
    )
    assert payload["ok"] is False
    assert "推論に失敗" in payload["error"]


def test_handle_request_returns_detections():
    model = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    payload = worker_main.handle_request(
        [("m.pt", model)], '{"image": "img.png", "conf": 0.25, "device": ""}'
    )
    assert payload["ok"] is True
    assert len(payload["detections"]) == 1
