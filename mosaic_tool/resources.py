"""アプリに同梱するリソースの場所を解決する。"""
from pathlib import Path

from PySide6.QtGui import QIcon


def app_icon_path() -> Path:
    # PyInstaller の onefile でも assets/ 配下に同梱するため、相対構成は開発時と同じ
    # (mosaic_tool/ パッケージの 1 つ上がリポジトリ直下 / 展開先ルートに対応する)
    return Path(__file__).resolve().parent.parent / "assets" / "icon.ico"


def load_app_icon() -> QIcon:
    icon_path = app_icon_path()
    if not icon_path.is_file():
        raise FileNotFoundError(f"アプリアイコンが見つかりません: {icon_path}")
    return QIcon(str(icon_path))
