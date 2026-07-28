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
