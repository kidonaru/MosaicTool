import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from mosaic_tool.resources import app_icon_path, load_app_icon


def test_app_icon_exists_and_qt_can_load_it():
    QApplication.instance() or QApplication([])

    assert app_icon_path().name == "icon.ico"
    assert app_icon_path().is_file()
    assert not load_app_icon().isNull()


def test_app_icon_is_resolved_from_bundle_dir_when_frozen(monkeypatch, tmp_path):
    # PyInstaller 展開先(.app の Contents/Frameworks 等)を基準に解決すること
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert app_icon_path() == tmp_path / "assets" / "icon.ico"
