"""ワーカークライアントのコマンド組み立て・スクリプト設置・応答の切り出しの検証"""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect import paths, worker_client  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_worker_command_lists_models_as_arguments():
    python = Path("C:/rt/python.exe")
    script = Path("C:/rt/detect_worker.py")
    models = [Path("C:/m/a.pt"), Path("C:/m/b.pt")]
    cmd = worker_client.worker_command(python, script, models)
    assert cmd == [str(python), str(script), str(models[0]), str(models[1])]


def test_install_worker_script_copies_source(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "runtime").mkdir()
    installed = worker_client.install_worker_script()
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == paths.worker_script_source().read_text(
        encoding="utf-8"
    )


def test_install_worker_script_overwrites_stale_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "runtime").mkdir()
    stale = paths.worker_script_installed()
    stale.write_text("古い内容", encoding="utf-8")
    worker_client.install_worker_script()
    assert stale.read_text(encoding="utf-8") != "古い内容"


def _worker(qapp) -> worker_client.DetectWorker:
    return worker_client.DetectWorker()


def test_feed_emits_detections_for_complete_line(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(json.dumps({"ok": True, "detections": [{"bbox": [0, 0, 1, 1]}]}) + "\n")
    assert received == [[{"bbox": [0, 0, 1, 1]}]]


def test_feed_waits_for_the_newline(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    payload = json.dumps({"ok": True, "detections": []})
    worker._feed(payload[:10])
    assert received == []
    worker._feed(payload[10:] + "\n")
    assert received == [[]]


def test_feed_emits_failure_for_error_response(qapp):
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(json.dumps({"ok": False, "error": "推論に失敗"}) + "\n")
    assert errors and "推論に失敗" in errors[0]


def test_ready_line_is_not_reported_as_detection(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    assert received == []


def test_startup_error_is_reported_as_failure(qapp):
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker._feed(json.dumps({"ok": False, "error": "モデルの読み込みに失敗しました"}) + "\n")
    assert errors and "モデルの読み込み" in errors[0]


def test_feed_emits_progress(qapp):
    worker = _worker(qapp)
    seen = []
    worker.progress.connect(lambda done, total, name: seen.append((done, total, name)))
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(
        json.dumps({"ok": True, "progress": {"done": 1, "total": 2, "model": "a.pt"}})
        + "\n"
    )
    assert seen == [(1, 2, "a.pt")]


def test_progress_is_not_reported_as_detection(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(
        json.dumps({"ok": True, "progress": {"done": 1, "total": 1, "model": "a.pt"}})
        + "\n"
    )
    assert received == []


def test_detections_arrive_even_without_a_ready_line(qapp):
    # 応答は種別で判別するため、ready の有無に依存しない
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "detections": []}) + "\n")
    assert received == [[]]


def test_request_without_models_reports_failure(qapp):
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker.request("img.png", {}, "")
    assert errors and "モデル" in errors[0]


def test_feed_emits_classes(qapp):
    worker = _worker(qapp)
    received = []
    worker.classes_received.connect(received.append)
    worker._feed(json.dumps({"ok": True, "ready": True}) + "\n")
    worker._feed(json.dumps({"ok": True, "classes": {"m.pt": ["face"]}}) + "\n")
    assert received == [{"m.pt": ["face"]}]


def test_classes_are_not_reported_as_detections(qapp):
    worker = _worker(qapp)
    received = []
    worker.detected.connect(received.append)
    worker._feed(json.dumps({"ok": True, "classes": {"m.pt": ["face"]}}) + "\n")
    assert received == []


def test_request_classes_without_any_model_reports_failure(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "models").mkdir()
    worker = _worker(qapp)
    errors = []
    worker.failed.connect(errors.append)
    worker.request_classes()
    assert errors and "モデル" in errors[0]


def test_request_classes_is_ignored_while_busy(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "models").mkdir()
    worker = _worker(qapp)
    worker._busy = True
    errors = []
    worker.failed.connect(errors.append)
    worker.request_classes()
    # 検出中は黙って無視する(エラーにはしない)
    assert errors == []
