"""Finder / Dock からのファイルオープン (QFileOpenEvent) の検証"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.application import MosaicApplication  # noqa: E402


class _FakeFileOpenEvent:
    """QFileOpenEvent の代役(type() と file() だけ使う)"""

    def __init__(self, path: Path):
        self._path = path

    def type(self):
        return QEvent.Type.FileOpen

    def file(self):
        return str(self._path)


class _FakeWindow:
    def __init__(self):
        self.opened: list[list[Path]] = []

    def open_paths(self, paths):
        self.opened.append(list(paths))


@pytest.fixture
def app():
    # QApplication はプロセスに 1 つしか作れないため、既にあるものを使い回す
    existing = QApplication.instance()
    if existing is not None and not isinstance(existing, MosaicApplication):
        pytest.skip("既に別種の QApplication が生成されているため検証できません")
    application = existing or MosaicApplication([])
    application.shutdown()  # 前のテストの持ち越しを消す
    yield application
    application.shutdown()


def test_file_open_event_is_forwarded_to_window(app, tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"")
    window = _FakeWindow()
    app.set_window(window)

    app.event(_FakeFileOpenEvent(image))

    assert window.opened == [[image]]


def test_file_open_before_window_is_replayed(app, tmp_path):
    """ウィンドウ生成前に届いたイベントは、生成後にまとめて流す"""
    image = tmp_path / "a.png"
    image.write_bytes(b"")
    app.event(_FakeFileOpenEvent(image))
    window = _FakeWindow()

    app.set_window(window)

    assert window.opened == [[image]]


def test_missing_file_is_ignored(app, tmp_path):
    window = _FakeWindow()
    app.set_window(window)

    app.event(_FakeFileOpenEvent(tmp_path / "missing.png"))

    assert window.opened == []
