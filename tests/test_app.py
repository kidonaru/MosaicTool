"""MainWindow のプレビュー操作(Tab ショートカット・画像切替時の解除)の検証"""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QToolBar  # noqa: E402

from PIL import Image  # noqa: E402

from mosaic_tool.app import PEN_STEP, THRESHOLD_STEP, MainWindow  # noqa: E402
from mosaic_tool.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def runtime_ready(monkeypatch):
    """推論環境ありを既定にする

    未構築だと自動検出がセットアップダイアログを開いて止まるため。
    未構築の挙動を見るテストは各自で False へ上書きする。
    """
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)


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


def test_toolbar_has_no_confidence_spinbox(window):
    # 信頼度はモデルごとの設定に一本化した
    assert not hasattr(window, "_confidence_spin")


def test_detect_action_opens_the_window(window):
    window._detect_act.trigger()
    assert window._detect_window is not None
    assert window._detect_window.isVisible() is True
    window._detect_window.close()


def test_detect_window_is_reused(window):
    window._detect_act.trigger()
    first = window._detect_window
    window._detect_act.trigger()
    assert window._detect_window is first
    first.close()


def test_detect_window_learns_whether_an_image_is_open(window):
    window._detect_act.trigger()
    # フィクスチャは画像 2 枚を開いた状態
    assert window._detect_window._image_available is True
    window._detect_window.close()


def test_start_detect_sends_the_models_to_the_worker(window, monkeypatch):
    sent = []
    monkeypatch.setattr(
        window._worker,
        "request",
        lambda image, models, device: sent.append((image, models, device)),
    )
    window._detect_act.trigger()
    window._start_detect({"a.pt": 0.25})
    assert sent and sent[0][1] == {"a.pt": 0.25}
    window._detect_window.close()


def test_detect_failure_restores_the_window(window, monkeypatch):
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical", lambda *args, **kwargs: None
    )
    window._detect_act.trigger()
    window._detect_window.set_running(True)
    window._on_detect_failed("検出に失敗しました")
    assert window._detect_window._running is False
    window._detect_window.close()


def test_closing_the_main_window_closes_the_detect_window(window):
    window._detect_act.trigger()
    detect_window = window._detect_window
    window.close()
    assert detect_window.isVisible() is False


def test_detect_action_opens_setup_first_when_runtime_is_missing(window, monkeypatch):
    # 未構築なら検出ウィンドウより先にセットアップを出す
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: False)
    opened = []

    class FakeSetupDialog:
        def __init__(self, parent=None):
            opened.append(True)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("mosaic_tool.app.RuntimeSetupDialog", FakeSetupDialog)
    window._detect_act.trigger()
    assert opened
    # セットアップを断ったら検出ウィンドウは出さない(構築前は何もできない)
    assert window._detect_window is None


def test_detect_window_opens_after_a_successful_setup(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: False)

    class FakeSetupDialog:
        def __init__(self, parent=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("mosaic_tool.app.RuntimeSetupDialog", FakeSetupDialog)
    window._detect_act.trigger()
    assert window._detect_window is not None
    window._detect_window.close()


def test_setup_is_not_shown_when_the_runtime_is_ready(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)
    opened = []
    monkeypatch.setattr(
        "mosaic_tool.app.RuntimeSetupDialog", lambda parent=None: opened.append(True)
    )
    window._detect_act.trigger()
    assert opened == []
    window._detect_window.close()
