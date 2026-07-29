"""セットアップダイアログの既定選択とモデル取得の進行の検証"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from mosaic_tool.detect import downloader, paths, setup_dialog  # noqa: E402
from mosaic_tool.detect.catalog import CatalogModel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeDownloader(QObject):
    """start() を呼ばれても通信せず、テストから結果を流し込めるようにする"""

    progress = Signal(int, int)
    retrying = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = []

    def start(self, url, destination, sha256=""):
        self.calls.append((url, destination, sha256))

    def cancel(self):
        pass


def test_cpu_is_selected_by_default(qapp, monkeypatch):
    # GPU があると判定される環境でも既定は CPU(選択肢を出す OS でのみ意味を持つ)
    monkeypatch.setattr(setup_dialog.runtime, "supports_gpu_choice", lambda: True)
    monkeypatch.setattr(setup_dialog, "has_nvidia_gpu", lambda: True)
    dialog = setup_dialog.RuntimeSetupDialog()
    assert dialog._cpu_radio.isChecked() is True
    assert dialog._gpu_radio.isChecked() is False


def test_model_download_starts_after_the_runtime_is_built(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    pending = [
        CatalogModel("a.pt", "顔", 1.0, 25, "aa"),
        CatalogModel("b.pt", "目", 1.0, 40, "bb"),
    ]
    monkeypatch.setattr(downloader, "pending_models", lambda: pending)
    fake = FakeDownloader()
    monkeypatch.setattr(setup_dialog, "ModelDownloader", lambda parent=None: fake)
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    assert fake.calls and fake.calls[0][1].name == "a.pt"


def test_all_models_are_downloaded_in_order(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    pending = [
        CatalogModel("a.pt", "顔", 1.0, 25, "aa"),
        CatalogModel("b.pt", "目", 1.0, 40, "bb"),
    ]
    monkeypatch.setattr(downloader, "pending_models", lambda: pending)
    fake = FakeDownloader()
    monkeypatch.setattr(setup_dialog, "ModelDownloader", lambda parent=None: fake)
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    dialog._on_download_finished(True, "取得しました: a.pt")
    assert [c[1].name for c in fake.calls] == ["a.pt", "b.pt"]
    dialog._on_download_finished(True, "取得しました: b.pt")
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_download_failure_still_completes_the_setup(qapp, monkeypatch, tmp_path):
    # venv さえあれば手動でモデルを置いて使えるため、ここで失敗にしない
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(
        downloader, "pending_models", lambda: [CatalogModel("a.pt", "顔", 1.0, 25, "aa")]
    )
    monkeypatch.setattr(
        setup_dialog, "ModelDownloader", lambda parent=None: FakeDownloader()
    )
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    dialog._on_download_finished(False, "ダウンロードに失敗しました: 通信エラー")
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert "失敗" in dialog._log.toPlainText()


def test_runtime_failure_does_not_start_downloads(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(
        downloader, "pending_models", lambda: [CatalogModel("a.pt", "顔", 1.0, 25, "aa")]
    )
    fake = FakeDownloader()
    monkeypatch.setattr(setup_dialog, "ModelDownloader", lambda parent=None: fake)
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(False, "セットアップに失敗しました")
    assert fake.calls == []
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_setup_accepts_immediately_when_no_model_is_pending(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "pending_models", lambda: [])
    dialog = setup_dialog.RuntimeSetupDialog()
    dialog._on_runtime_finished(True, "完了")
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_macos_hides_gpu_choice(monkeypatch, qapp):
    # macOS はインストール内容が 1 通りしかないため選択肢を出さない
    monkeypatch.setattr(setup_dialog.runtime, "supports_gpu_choice", lambda: False)
    dialog = setup_dialog.RuntimeSetupDialog()
    assert dialog._gpu_radio is None
    assert dialog._cpu_radio is None


def test_macos_start_requests_cpu_install(monkeypatch, qapp):
    monkeypatch.setattr(setup_dialog.runtime, "supports_gpu_choice", lambda: False)
    dialog = setup_dialog.RuntimeSetupDialog()
    called = []
    monkeypatch.setattr(dialog._installer, "start", lambda use_gpu: called.append(use_gpu))
    dialog._start()
    assert called == [False]
