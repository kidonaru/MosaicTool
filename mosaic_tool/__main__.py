"""エントリポイント: exe/スクリプトへの D&D は引数としてパスが渡される"""
import sys
from pathlib import Path

from mosaic_tool.openssl_preload import preload_bundled_openssl

# Qt が OpenSSL をファイル名で探し始める前に同梱版をフルパスで確定させる。
# System32 の別バージョンと混ざるのを防ぐため、PySide6 の import より前に行う
preload_bundled_openssl()

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.app import MainWindow  # noqa: E402
from mosaic_tool.resources import load_app_icon  # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    paths = [a for a in sys.argv[1:] if Path(a).exists()]
    win = MainWindow(paths)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
