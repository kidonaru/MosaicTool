"""エントリポイント: 実行ファイルへの D&D は引数としてパスが渡される

macOS では引数ではなく QFileOpenEvent で届くため、MosaicApplication が受け取る。
"""
import sys
from pathlib import Path

from mosaic_tool.app import MainWindow
from mosaic_tool.application import MosaicApplication
from mosaic_tool.resources import load_app_icon


def main():
    app = MosaicApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    paths = [a for a in sys.argv[1:] if Path(a).exists()]
    win = MainWindow(paths)
    app.set_window(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
