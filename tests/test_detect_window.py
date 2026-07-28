"""自動検出ウィンドウのモデル一覧・設定連動・実行可否の検証"""
import os

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent

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
    python = paths.venv_python()
    python.parent.mkdir(parents=True)
    python.write_bytes(b"x")
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


def test_enabled_models_returns_confidence_and_classes(window):
    window._rows["unknown.pt"].check.setChecked(False)
    assert window.enabled_models() == {
        "Anzhc Eyes -seg-hd.pt": {"conf": 0.4, "classes": []}
    }


def test_unchecking_disables_the_slider(window):
    row = window._rows["unknown.pt"]
    row.check.setChecked(False)
    assert row.slider.isEnabled() is False


def test_wheel_does_not_change_the_slider(window):
    # 一覧をスクロールするつもりのホイールで信頼度が変わらないこと
    row = window._rows["unknown.pt"]
    before = row.slider.value()
    pos = QPointF(row.slider.rect().center())
    row.slider.wheelEvent(
        QWheelEvent(
            pos, pos, QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
    )
    assert row.slider.value() == before


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
    assert received == [{"Anzhc Eyes -seg-hd.pt": {"conf": 0.4, "classes": []}}]


def test_detect_all_requested_carries_the_enabled_models(window):
    window._rows["unknown.pt"].check.setChecked(False)
    received = []
    window.detect_all_requested.connect(received.append)
    window._on_detect_all_clicked()
    assert received == [{"Anzhc Eyes -seg-hd.pt": {"conf": 0.4, "classes": []}}]


def test_detect_all_button_follows_the_detect_button(window):
    # 実行可否の条件は 1 枚だけの検出と同じ
    window.set_image_available(False)
    assert window._detect_all_button.isEnabled() is False
    window.set_image_available(True)
    assert window._detect_all_button.isEnabled() is True


def test_running_state_disables_the_detect_button(window):
    window.set_running(True)
    assert window._detect_button.isEnabled() is False
    assert window._detect_all_button.isEnabled() is False
    window.set_running(False)
    assert window._detect_button.isEnabled() is True
    assert window._detect_all_button.isEnabled() is True


def test_running_state_deactivates_the_model_area(window):
    window.set_running(True)
    assert window._group.isEnabled() is False
    assert window._setup_button.isEnabled() is False
    window.set_running(False)
    assert window._group.isEnabled() is True
    assert window._setup_button.isEnabled() is True


def test_running_state_shows_loading_until_progress_arrives(window):
    window.set_running(True)
    assert window._status_label.isVisibleTo(window) is True
    # 進捗が読めないうちは不確定表示にする
    assert (window._bar.minimum(), window._bar.maximum()) == (0, 0)
    window.set_progress(1, 3)
    assert window._status_label.isVisibleTo(window) is False


def test_progress_bar_reflects_the_reported_counts(window):
    window.set_progress(1, 3)
    # ウィンドウを表示していないため isVisible() ではなく isVisibleTo() で見る
    assert window._bar.isVisibleTo(window) is True
    assert (window._bar.value(), window._bar.maximum()) == (1, 3)


def test_progress_bar_is_hidden_when_the_run_ends(window):
    window.set_progress(1, 3)
    window.set_running(False)
    assert window._bar.isVisibleTo(window) is False


def test_refresh_shows_loading_while_it_runs(window, monkeypatch):
    """更新の最中はモデル一覧を非アクティブにしてロード中を出す"""
    seen = {}
    original = paths.model_files

    def spy():
        seen["enabled"] = window._group.isEnabled()
        seen["label"] = window._status_label.isVisibleTo(window)
        seen["text"] = window._status_label.text()
        return original()

    monkeypatch.setattr(paths, "model_files", spy)
    window.refresh()
    assert seen == {"enabled": False, "label": True, "text": "モデルを更新中..."}
    # 終わったら元に戻る
    assert window._group.isEnabled() is True
    assert window._status_label.isVisibleTo(window) is False
    assert window._bar.isVisibleTo(window) is False


def test_refresh_during_a_run_keeps_the_running_display(window):
    window.set_running(True)
    window.refresh()
    assert window._group.isEnabled() is False
    assert window._status_label.text() == "モデルを読み込み中..."


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


def test_enabled_models_carries_the_saved_classes(window):
    window._settings.set_model_classes("unknown.pt", ["penis"])
    window.refresh()
    assert window.enabled_models()["unknown.pt"]["classes"] == ["penis"]


def test_class_button_shows_the_selected_count(window):
    assert window._rows["unknown.pt"].class_button.text() == "クラス…"
    window._settings.set_model_classes("unknown.pt", ["penis", "pussy"])
    window.refresh()
    assert window._rows["unknown.pt"].class_button.text() == "クラス (2件)"


def test_unchecking_disables_the_class_button(window):
    row = window._rows["unknown.pt"]
    row.check.setChecked(False)
    assert row.class_button.isEnabled() is False


def test_class_button_emits_the_request_and_waits(window):
    seen = []
    window.classes_requested.connect(lambda: seen.append(1))
    window._on_class_clicked("unknown.pt")
    assert seen == [1]
    assert window._pending_class_model == "unknown.pt"
    assert window._group.isEnabled() is False


def test_cancelling_the_class_request_restores_the_display(window):
    window._on_class_clicked("unknown.pt")
    window.cancel_class_request()
    assert window._pending_class_model is None
    assert window._group.isEnabled() is True


def test_accepting_the_dialog_saves_the_selection(window, monkeypatch, tmp_path):
    from mosaic_tool.detect import detect_window as module

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return module.QDialog.DialogCode.Accepted

        def selected_classes(self):
            return ["penis"]

    monkeypatch.setattr(module, "ClassSelectDialog", FakeDialog)
    window._on_class_clicked("unknown.pt")
    window.show_class_selection({"unknown.pt": ["face", "penis"]})
    assert window._pending_class_model is None
    reopened = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    assert reopened.model_classes("unknown.pt") == ["penis"]
    assert window._rows["unknown.pt"].class_button.text() == "クラス (1件)"


def test_cancelling_the_dialog_keeps_the_selection(window, monkeypatch):
    from mosaic_tool.detect import detect_window as module

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return module.QDialog.DialogCode.Rejected

        def selected_classes(self):
            return ["penis"]

    monkeypatch.setattr(module, "ClassSelectDialog", FakeDialog)
    window._on_class_clicked("unknown.pt")
    window.show_class_selection({"unknown.pt": ["face", "penis"]})
    assert window._settings.model_classes("unknown.pt") == []


def test_class_selection_for_an_unknown_model_is_dropped(window):
    # 応答が届く前に models\ が変わった場合
    window._on_class_clicked("unknown.pt")
    window.show_class_selection({"other.pt": ["face"]})
    assert window._pending_class_model is None
    assert window._group.isEnabled() is True
