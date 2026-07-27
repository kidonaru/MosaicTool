"""エントリポイント: exe/スクリプトへの D&D は引数としてパスが渡される"""
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mosaic_tool.app import MainWindow
from mosaic_tool.resources import load_app_icon


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    paths = [a for a in sys.argv[1:] if Path(a).exists()]
    win = MainWindow(paths)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
