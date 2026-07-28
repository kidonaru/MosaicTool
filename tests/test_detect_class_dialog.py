"""クラス選択ダイアログの初期状態・全選択/全解除・OK の可否の検証"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.detect.class_dialog import ClassSelectDialog  # noqa: E402

CLASSES = ["face", "penis", "pussy"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_empty_selection_means_every_class(qapp):
    dialog = ClassSelectDialog("m.pt", CLASSES, [])
    assert dialog.selected_classes() == CLASSES
    dialog.close()


def test_saved_selection_is_restored(qapp):
    dialog = ClassSelectDialog("m.pt", CLASSES, ["pussy", "face"])
    # 並び順はモデルのクラス順に揃える
    assert dialog.selected_classes() == ["face", "pussy"]
    dialog.close()


def test_names_the_model_does_not_have_are_ignored(qapp):
    dialog = ClassSelectDialog("m.pt", CLASSES, ["penis", "unknown"])
    assert dialog.selected_classes() == ["penis"]
    dialog.close()


def test_stale_selection_falls_back_to_every_class(qapp):
    # 保存済みの名前が 1 つも無いモデルへ差し替えられた場合
    dialog = ClassSelectDialog("m.pt", CLASSES, ["gone"])
    assert dialog.selected_classes() == CLASSES
    dialog.close()


def test_clear_all_disables_ok(qapp):
    dialog = ClassSelectDialog("m.pt", CLASSES, [])
    dialog._on_clear_all()
    assert dialog.selected_classes() == []
    assert dialog._ok_button.isEnabled() is False
    dialog._on_select_all()
    assert dialog._ok_button.isEnabled() is True
    dialog.close()


def test_model_name_is_shown(qapp):
    dialog = ClassSelectDialog("nsfw.pt", CLASSES, [])
    assert "nsfw.pt" in dialog._title_label.text()
    dialog.close()


def test_empty_class_list_is_reported(qapp):
    dialog = ClassSelectDialog("m.pt", [], [])
    assert dialog.selected_classes() == []
    assert dialog._ok_button.isEnabled() is False
    dialog.close()
