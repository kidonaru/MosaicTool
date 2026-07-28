"""ダウンロード対象の判定と一時ファイル名の検証(通信は行わない)"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect import catalog, downloader, paths  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_pending_models_lists_all_when_nothing_is_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert downloader.pending_models() == list(catalog.MODELS)


def test_pending_models_skips_installed_files(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / catalog.MODELS[0].filename).write_bytes(b"x")
    pending = downloader.pending_models()
    assert catalog.MODELS[0] not in pending
    assert len(pending) == len(catalog.MODELS) - 1


def test_part_path_appends_suffix():
    assert downloader.part_path(Path("C:/m/a.pt")) == Path("C:/m/a.pt.part")


def test_cancel_before_start_does_nothing(qapp):
    # 何も起きていない状態で呼んでも例外にならない
    downloader.ModelDownloader().cancel()


@pytest.fixture
def stub_downloader(qapp, tmp_path, monkeypatch):
    """実際の通信を行わず、取得開始の回数だけ数えるダウンローダ"""
    monkeypatch.setattr(downloader, "RETRY_DELAY_MS", 0)  # 待たずに再試行させる
    dl = downloader.ModelDownloader()
    dl.requests = []
    monkeypatch.setattr(dl, "_request", lambda: dl.requests.append(dl._destination))
    dl.start("https://example.invalid/a.pt", tmp_path / "a.pt")
    return dl


def test_retry_starts_another_request_after_the_delay(qapp, stub_downloader):
    notices = []
    stub_downloader.retrying.connect(notices.append)
    stub_downloader._schedule_retry("ダウンロードに失敗しました")
    qapp.processEvents()
    assert len(notices) == 1
    assert len(stub_downloader.requests) == 2


def test_cancel_while_waiting_for_retry_reports_the_abort(qapp, stub_downloader):
    results = []
    stub_downloader.finished.connect(lambda ok, msg: results.append((ok, msg)))
    stub_downloader._schedule_retry("ダウンロードに失敗しました")
    stub_downloader.cancel()
    qapp.processEvents()
    assert results == [(False, downloader.CANCELLED_TEXT)]
    assert len(stub_downloader.requests) == 1  # 再試行は動かない


def test_cancel_after_completion_does_not_emit_again(stub_downloader):
    results = []
    stub_downloader.finished.connect(lambda ok, msg: results.append(msg))
    stub_downloader._emit_finished(True, "取得しました")
    stub_downloader.cancel()
    assert results == ["取得しました"]
