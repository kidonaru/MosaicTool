"""テスト全体で共有する QApplication の用意

QApplication はプロセスに 1 つしか作れない。各テストは
`QApplication.instance() or QApplication([])` で取得するため、最初に生成される
1 つが全モジュールで使い回される。ここで MosaicApplication を先に作っておくと、
QFileOpenEvent の検証(tests/test_application.py)も実行順に左右されずに行える。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.application import MosaicApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _qapplication():
    return QApplication.instance() or MosaicApplication([])
