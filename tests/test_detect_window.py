"""自動検出ウィンドウのモデル一覧・設定連動・実行可否の検証"""
import os

import pytest
from PySide6.QtCore import QSettings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect import paths  # noqa: E402
from mosaic_tool.detect.detect_window import DetectWindow  # noqa: E402
from mosaic_tool.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    """models/ と runtime/ を持つ一時ディレクトリを基準にしたウィンドウ"""
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "Anzhc Eyes -seg-hd.pt").write_bytes(b"x")
    (models / "unknown.pt").write_bytes(b"x")
    (tmp_path / "runtime" / "Scripts").mkdir(parents=True)
    (tmp_path / "runtime" / "Scripts" / "python.exe").write_bytes(b"x")
    settings = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    win = DetectWindow(settings)
    win.set_image_available(True)
    yield win
    win.close()


def test_lists_models_from_the_models_directory(window):
    assert set(window._rows) == {"Anzhc Eyes -seg-hd.pt", "unknown.pt"}


def test_catalog_model_uses_its_recommended_confidence(window):
    assert window._rows["Anzhc Eyes -seg-hd.pt"].slider.value() == 40


def test_unknown_model_falls_back_to_the_shared_default(window):
    assert window._rows["unknown.pt"].slider.value() == 25


def test_catalog_model_shows_its_label(window):
    assert window._rows["Anzhc Eyes -seg-hd.pt"].label.text() == "目"
    assert window._rows["unknown.pt"].label.text() == ""


def test_enabled_models_returns_confidence_as_ratio(window):
    window._rows["unknown.pt"].check.setChecked(False)
    assert window.enabled_models() == {"Anzhc Eyes -seg-hd.pt": 0.4}


def test_unchecking_disables_the_slider(window):
    row = window._rows["unknown.pt"]
    row.check.setChecked(False)
    assert row.slider.isEnabled() is False


def test_confidence_change_is_persisted(window, tmp_path):
    window._rows["unknown.pt"].slider.setValue(70)
    reopened = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    assert reopened.model_confidence("unknown.pt", 1, 100) == 70


def test_enabled_state_is_persisted(window, tmp_path):
    window._rows["Anzhc Eyes -seg-hd.pt"].check.setChecked(False)
    reopened = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    assert reopened.model_enabled("Anzhc Eyes -seg-hd.pt") is False


def test_detect_button_is_disabled_without_any_enabled_model(window):
    for row in window._rows.values():
        row.check.setChecked(False)
    assert window._detect_button.isEnabled() is False


def test_detect_button_is_disabled_without_an_image(window):
    window.set_image_available(False)
    assert window._detect_button.isEnabled() is False


def test_detect_requested_carries_the_enabled_models(window):
    window._rows["unknown.pt"].check.setChecked(False)
    received = []
    window.detect_requested.connect(received.append)
    window._on_detect_clicked()
    assert received == [{"Anzhc Eyes -seg-hd.pt": 0.4}]


def test_running_state_disables_the_detect_button(window):
    window.set_running(True)
    assert window._detect_button.isEnabled() is False
    window.set_running(False)
    assert window._detect_button.isEnabled() is True


def test_progress_bar_reflects_the_reported_counts(window):
    window.set_progress(1, 3)
    # ウィンドウを表示していないため isVisible() ではなく isVisibleTo() で見る
    assert window._bar.isVisibleTo(window) is True
    assert (window._bar.value(), window._bar.maximum()) == (1, 3)


def test_progress_bar_is_hidden_when_the_run_ends(window):
    window.set_progress(1, 3)
    window.set_running(False)
    assert window._bar.isVisibleTo(window) is False


def test_refresh_picks_up_new_files(window, tmp_path):
    (tmp_path / "models" / "new.pt").write_bytes(b"x")
    window.refresh()
    assert "new.pt" in window._rows


def test_refresh_emits_models_changed_only_when_files_change(window, tmp_path):
    seen = []
    window.models_changed.connect(lambda: seen.append(1))
    window.refresh()
    assert seen == []
    (tmp_path / "models" / "new.pt").write_bytes(b"x")
    window.refresh()
    assert seen == [1]


def test_runtime_missing_disables_the_model_area(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    (tmp_path / "models").mkdir()
    settings = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    win = DetectWindow(settings)
    win.set_image_available(True)
    assert win._detect_button.isEnabled() is False
    assert "未構築" in win._runtime_label.text()
    win.close()


def test_model_rows_are_packed_at_the_top(window):
    # 末尾に伸縮する余白行を置き、行間が広がらないようにする
    rows = len(window._rows)
    assert window._grid.rowStretch(rows) == 1
    assert all(window._grid.rowStretch(i) == 0 for i in range(rows))


def test_stale_stretch_is_cleared_when_the_list_shrinks(window, tmp_path):
    (tmp_path / "models" / "unknown.pt").unlink()
    window.refresh()
    rows = len(window._rows)
    assert window._grid.rowStretch(rows) == 1
    assert window._grid.rowStretch(rows + 1) == 0
