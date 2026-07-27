"""MainWindow のプレビュー操作(Tab ショートカット・画像切替時の解除)の検証"""
import os

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
