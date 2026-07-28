"""アプリに同梱するリソースの場所を解決する。"""
from pathlib import Path

from PySide6.QtGui import QIcon

from mosaic_tool.bundle import bundle_dir


def app_icon_path() -> Path:
    # PyInstaller では __file__ が実在しないため、展開先(sys._MEIPASS)を基準にする
    return bundle_dir() / "assets" / "icon.ico"


def load_app_icon() -> QIcon:
    icon_path = app_icon_path()
    if not icon_path.is_file():
        raise FileNotFoundError(f"アプリアイコンが見つかりません: {icon_path}")
    return QIcon(str(icon_path))
