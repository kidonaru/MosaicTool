import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from mosaic_tool.resources import app_icon_path, load_app_icon


def test_app_icon_exists_and_qt_can_load_it():
    QApplication.instance() or QApplication([])

    assert app_icon_path().name == "icon.ico"
    assert app_icon_path().is_file()
    assert not load_app_icon().isNull()
