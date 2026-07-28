"""MainWindow のプレビュー操作(Tab ショートカット・画像切替時の解除)の検証"""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402

from PIL import Image  # noqa: E402

from mosaic_tool.app import PEN_STEP, THRESHOLD_STEP, MainWindow  # noqa: E402
from mosaic_tool.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    """実設定を汚さないよう一時 ini を使い、画像 2 枚を開いたウィンドウを返す"""
    images = []
    for i in range(2):
        path = tmp_path / f"img{i}.png"
        Image.new("RGB", (40, 30), (i * 100, 0, 0)).save(path)
        images.append(path)
    settings = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    win = MainWindow([str(p) for p in images], settings=settings)
    yield win
    win.close()


def test_preview_shortcut_is_tab(window):
    assert window._preview_act.shortcut() == QKeySequence(Qt.Key.Key_Tab)


def test_toolbar_actions_show_key_in_tooltip(window):
    expected = {
        "ペン": "ペン (1)",
        "矩形": "矩形 (2)",
        "◀ 前へ": "◀ 前へ (Left)",
        "次へ ▶": "次へ ▶ (Right)",
        "保存": "保存 (Ctrl+S)",
        "プレビュー": "プレビュー (Tab)",
        "自動検出": "自動検出 (D)",
    }
    tooltips = {
        act.text(): act.toolTip()
        for act in window.findChild(QToolBar).actions()
        if act.text()
    }
    assert tooltips == expected


def test_single_key_shortcuts_are_scoped_to_canvas(window):
    """修飾キーなしのキーがスピンボックスへの入力を奪わないこと(保存のみ全体)"""
    scoped = Qt.ShortcutContext.WidgetWithChildrenShortcut
    for act in (window._preview_act, window._prev_act, window._next_act):
        assert act.shortcutContext() == scoped
        assert act in window.canvas.actions()
    for act in window._mode_group.actions():
        assert act.shortcutContext() == scoped
    save_act = next(
        a for a in window.findChild(QToolBar).actions() if a.text() == "保存"
    )
    assert save_act.shortcutContext() == Qt.ShortcutContext.WindowShortcut
    assert save_act not in window.canvas.actions()


def test_spinboxes_step_by_five_but_accept_any_value(window):
    """矢印ボタンは 5 刻み、数値入力は 1 刻みで受け付ける"""
    assert window._threshold_spin.singleStep() == THRESHOLD_STEP
    assert window._pen_spin.singleStep() == PEN_STEP

    window._threshold_spin.setValue(13)
    assert window._threshold_spin.value() == 13
    assert window.canvas._threshold == pytest.approx(0.13)

    window._pen_spin.setValue(37)
    assert window._pen_spin.value() == 37
    assert window.canvas._pen_width == pytest.approx(37.0)


def test_preview_toggle_updates_canvas(window):
    window._preview_act.setChecked(True)
    assert window.canvas._preview
    window._preview_act.setChecked(False)
    assert not window.canvas._preview


def test_preview_cleared_on_navigation(window):
    window._preview_act.setChecked(True)
    window._go(1)
    assert window._index == 1
    assert not window._preview_act.isChecked()
    assert not window.canvas._preview


def test_detect_action_shortcut_is_d(window):
    assert window._detect_act.shortcut() == QKeySequence(Qt.Key.Key_D)


def test_confidence_spin_restores_setting(qapp, tmp_path):
    settings = AppSettings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    settings.set_confidence(40)
    win = MainWindow(settings=settings)
    try:
        assert win._confidence_spin.value() == 40
    finally:
        win.close()


def test_confidence_change_is_saved(window):
    window._confidence_spin.setValue(55)
    assert window._settings.confidence(1, 100) == 55


def test_detected_regions_are_added_to_canvas(window):
    window._on_detected([{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}])
    assert len(window.canvas.get_regions()) == 2


def test_detected_regions_can_be_undone_at_once(window):
    window._on_detected([{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}])
    window.canvas.undo()
    assert window.canvas.get_regions() == []


def test_empty_detection_shows_message(window):
    window._on_detected([])
    assert "検出されませんでした" in window.statusBar().currentMessage()


def test_detect_failure_shows_error(window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical",
        lambda *args, **kwargs: shown.append(args[2]),
    )
    window._on_detect_failed("モデルの読み込みに失敗しました")
    assert shown and "モデルの読み込み" in shown[0]


def test_detect_without_models_warns_and_does_not_start(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)
    monkeypatch.setattr("mosaic_tool.app.detect_paths.model_files", lambda: [])
    warned = []
    monkeypatch.setattr(window, "_warn_models_missing", lambda: warned.append(True))
    requested = []
    monkeypatch.setattr(window._worker, "request", lambda *a: requested.append(a))
    window._on_detect()
    assert warned and not requested


def test_detect_starts_worker_when_ready(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)
    monkeypatch.setattr(
        "mosaic_tool.app.detect_paths.model_files", lambda: [Path("dummy.pt")]
    )
    requested = []
    monkeypatch.setattr(window._worker, "request", lambda *a: requested.append(a))
    window._on_detect()
    assert len(requested) == 1
    # (画像パス, 信頼度 0.0-1.0, デバイス)
    assert requested[0][1] == window._confidence_spin.value() / 100
