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

    def __init__(self, result, names=None):
        self._result = result
        self.names = names if names is not None else {0: "object"}
        self.calls = []

    def __call__(self, image, conf=None, device=None, verbose=None, classes=None):
        self.calls.append(
            {"image": image, "conf": conf, "device": device, "classes": classes}
        )
        return [self._result]


def test_worker_does_not_import_mosaic_tool():
    # venv 側には mosaic_tool が無いため、依存してはならない
    source = worker_script_source().read_text(encoding="utf-8")
    assert "mosaic_tool" not in source


def test_detect_returns_bbox_and_model_name():
    model = FakeModel(FakeResult([FakeBox(0.9, [1, 2, 3, 4])]))
    result = worker_main.detect(
        [("pussyV2.pt", model)], "img.png", {"pussyV2.pt": {"conf": 0.25}}, ""
    )
    assert result == [
        {"model": "pussyV2.pt", "conf": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]}
    ]


def test_detect_includes_polygon_when_masks_exist():
    model = FakeModel(
        FakeResult([FakeBox(0.9, [0, 0, 10, 10])], polygons=[[(0, 0), (10, 0), (10, 10)]])
    )
    result = worker_main.detect(
        [("m.pt", model)], "img.png", {"m.pt": {"conf": 0.25}}, ""
    )
    assert result[0]["polygon"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]


def test_detect_combines_multiple_models():
    a = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    b = FakeModel(FakeResult([FakeBox(0.8, [2, 2, 3, 3])]))
    result = worker_main.detect(
        [("a.pt", a), ("b.pt", b)],
        "img.png",
        {"a.pt": {"conf": 0.3}, "b.pt": {"conf": 0.3}},
        "cpu",
    )
    assert [d["model"] for d in result] == ["a.pt", "b.pt"]


def test_detect_uses_the_confidence_of_each_model():
    a = FakeModel(FakeResult([]))
    b = FakeModel(FakeResult([]))
    worker_main.detect(
        [("a.pt", a), ("b.pt", b)],
        "img.png",
        {"a.pt": {"conf": 0.25}, "b.pt": {"conf": 0.4}},
        "",
    )
    assert a.calls[0]["conf"] == 0.25
    assert b.calls[0]["conf"] == 0.4


def test_detect_skips_models_not_listed():
    used = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    unused = FakeModel(FakeResult([FakeBox(0.9, [2, 2, 3, 3])]))
    result = worker_main.detect(
        [("used.pt", used), ("unused.pt", unused)],
        "img.png",
        {"used.pt": {"conf": 0.3}},
        "",
    )
    assert unused.calls == []
    assert [d["model"] for d in result] == ["used.pt"]


def test_detect_reports_progress_per_model():
    a = FakeModel(FakeResult([]))
    b = FakeModel(FakeResult([]))
    seen = []
    worker_main.detect(
        [("a.pt", a), ("b.pt", b), ("skip.pt", FakeModel(FakeResult([])))],
        "img.png",
        {"a.pt": {"conf": 0.3}, "b.pt": {"conf": 0.3}},
        "",
        on_progress=lambda done, total, name: seen.append((done, total, name)),
    )
    # 総数は推論するモデルの件数(除外分は数えない)
    assert seen == [(1, 2, "a.pt"), (2, 2, "b.pt")]


def test_empty_device_is_passed_as_none():
    # 空文字は ultralytics へ渡さず自動選択に任せる
    model = FakeModel(FakeResult([]))
    worker_main.detect([("m.pt", model)], "img.png", {"m.pt": {"conf": 0.4}}, "")
    assert model.calls[0]["device"] is None


def test_handle_request_returns_error_payload_on_failure():
    class BrokenModel:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("推論に失敗")

    payload = worker_main.handle_request(
        [("m.pt", BrokenModel())],
        '{"image": "img.png", "models": {"m.pt": {"conf": 0.25}}}',
        lambda _payload: None,
    )
    assert payload["ok"] is False
    assert "推論に失敗" in payload["error"]


def test_handle_request_returns_detections():
    model = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    payload = worker_main.handle_request(
        [("m.pt", model)],
        '{"image": "img.png", "models": {"m.pt": {"conf": 0.25}}, "device": ""}',
        lambda _payload: None,
    )
    assert payload["ok"] is True
    assert len(payload["detections"]) == 1


def test_handle_request_emits_progress_payloads():
    emitted = []
    model = FakeModel(FakeResult([]))
    worker_main.handle_request(
        [("m.pt", model)],
        '{"image": "img.png", "models": {"m.pt": {"conf": 0.25}}}',
        emitted.append,
    )
    assert emitted == [
        {"ok": True, "progress": {"done": 1, "total": 1, "model": "m.pt"}}
    ]


def test_detect_passes_selected_class_ids():
    model = FakeModel(FakeResult([]), names={0: "face", 1: "penis", 2: "pussy"})
    worker_main.detect(
        [("m.pt", model)],
        "img.png",
        {"m.pt": {"conf": 0.3, "classes": ["pussy", "penis"]}},
        "",
    )
    # 名前 → ID へ変換して渡す(順序は model.names の並び)
    assert model.calls[0]["classes"] == [1, 2]


def test_detect_without_classes_asks_for_every_class():
    model = FakeModel(FakeResult([]), names={0: "face", 1: "penis"})
    worker_main.detect([("m.pt", model)], "img.png", {"m.pt": {"conf": 0.3}}, "")
    assert model.calls[0]["classes"] is None


def test_detect_ignores_class_names_the_model_does_not_have():
    model = FakeModel(FakeResult([]), names={0: "face", 1: "penis"})
    worker_main.detect(
        [("m.pt", model)],
        "img.png",
        {"m.pt": {"conf": 0.3, "classes": ["penis", "unknown"]}},
        "",
    )
    assert model.calls[0]["classes"] == [1]


def test_detect_falls_back_to_every_class_when_no_name_matches():
    # モデルを差し替えてクラス名が総入れ替えになっても、何も検出されない状態にはしない
    model = FakeModel(FakeResult([]), names={0: "face"})
    worker_main.detect(
        [("m.pt", model)], "img.png", {"m.pt": {"conf": 0.3, "classes": ["penis"]}}, ""
    )
    assert model.calls[0]["classes"] is None


def test_model_classes_lists_names_in_id_order():
    a = FakeModel(FakeResult([]), names={1: "penis", 0: "face"})
    b = FakeModel(FakeResult([]), names={0: "eye"})
    assert worker_main.model_classes([("a.pt", a), ("b.pt", b)]) == {
        "a.pt": ["face", "penis"],
        "b.pt": ["eye"],
    }


def test_handle_request_answers_the_classes_command():
    model = FakeModel(FakeResult([]), names={0: "face"})
    payload = worker_main.handle_request(
        [("m.pt", model)], '{"command": "classes"}', lambda _p: None
    )
    assert payload == {"ok": True, "classes": {"m.pt": ["face"]}}
    assert model.calls == []


def test_handle_request_defaults_to_detect_without_a_command():
    model = FakeModel(FakeResult([]))
    payload = worker_main.handle_request(
        [("m.pt", model)],
        '{"image": "img.png", "models": {"m.pt": {"conf": 0.25}}}',
        lambda _p: None,
    )
    assert payload["ok"] is True
    assert payload["detections"] == []


def test_handle_request_without_models_returns_empty_detections():
    model = FakeModel(FakeResult([FakeBox(0.9, [0, 0, 1, 1])]))
    payload = worker_main.handle_request(
        [("m.pt", model)], '{"image": "img.png", "models": {}}', lambda _p: None
    )
    assert payload["detections"] == []
    assert model.calls == []
