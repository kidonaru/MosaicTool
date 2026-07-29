"""Finder / Dock から渡されたファイルを受け取る QApplication

macOS ではコマンドライン引数ではなく QFileOpenEvent でパスが届く。
アプリ起動と同時にドロップされた場合はウィンドウより先にイベントが来るため、
いったん貯めておいてウィンドウが用意できた時点で流す。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


class MosaicApplication(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self._window = None
        self._pending: list[Path] = []

    def set_window(self, window) -> None:
        self._window = window
        if self._pending:
            window.open_paths(self._pending)
            self._pending = []

    def shutdown(self) -> None:
        """テストから明示的に後始末するためのフック"""
        self._window = None
        self._pending = []

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            path = Path(event.file())
            if path.exists():
                if self._window is None:
                    self._pending.append(path)
                else:
                    self._window.open_paths([path])
            return True
        return super().event(event)
