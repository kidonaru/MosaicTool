"""アプリ設定の永続化 (QSettings ラッパー)

モザイクサイズ・ペン太さ・自動保存などの UI 設定を保存し、次回起動時に復元する。
保存先は OS 標準の場所 (Windows ならレジストリ HKCU\\Software\\MosaicTool\\MosaicTool)。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from mosaic_tool.version import APP_NAME

ORG_NAME = APP_NAME

# 既定値
DEFAULT_BLOCK = 10        # モザイクサイズ (px)
DEFAULT_THRESHOLD = 10    # マス単位判定のしきい値 (%)
DEFAULT_PEN_WIDTH = 20    # ペン太さ (px)
DEFAULT_AUTOSAVE = True   # 自動保存
DEFAULT_MODE = "pen"      # ツールモード ("pen" / "rect")

_KEY_BLOCK = "mosaic/block"
_KEY_THRESHOLD = "mosaic/threshold"
_KEY_PEN_WIDTH = "tool/pen_width"
_KEY_AUTOSAVE = "save/autosave"
_KEY_MODE = "tool/mode"
_KEY_GEOMETRY = "window/geometry"


class AppSettings:
    """設定値の読み書き。値は都度 QSettings へ同期する"""

    def __init__(self, settings: QSettings | None = None):
        self._qsettings = settings or QSettings(ORG_NAME, APP_NAME)

    # --- 内部ヘルパ ---

    def _int(
        self, key: str, default: int, minimum: int, maximum: int, step: int = 1
    ) -> int:
        """int として読み出し、壊れた値・範囲外・刻み幅外は既定値に丸める"""
        try:
            value = int(self._qsettings.value(key, default))
        except (TypeError, ValueError):
            return default
        if not (minimum <= value <= maximum) or value % step != 0:
            return default
        return value

    # --- モザイクサイズ ---

    def block(self, minimum: int, maximum: int, step: int = 1) -> int:
        return self._int(_KEY_BLOCK, DEFAULT_BLOCK, minimum, maximum, step)

    def set_block(self, value: int) -> None:
        self._qsettings.setValue(_KEY_BLOCK, int(value))

    # --- しきい値 ---

    def threshold(self, minimum: int, maximum: int, step: int = 1) -> int:
        return self._int(_KEY_THRESHOLD, DEFAULT_THRESHOLD, minimum, maximum, step)

    def set_threshold(self, value: int) -> None:
        self._qsettings.setValue(_KEY_THRESHOLD, int(value))

    # --- ペン太さ ---

    def pen_width(self, minimum: int, maximum: int, step: int = 1) -> int:
        return self._int(_KEY_PEN_WIDTH, DEFAULT_PEN_WIDTH, minimum, maximum, step)

    def set_pen_width(self, value: int) -> None:
        self._qsettings.setValue(_KEY_PEN_WIDTH, int(value))

    # --- 自動保存 ---

    def autosave(self) -> bool:
        value = self._qsettings.value(_KEY_AUTOSAVE, DEFAULT_AUTOSAVE)
        if isinstance(value, str):
            return value.lower() in ("true", "1")
        return bool(value)

    def set_autosave(self, value: bool) -> None:
        self._qsettings.setValue(_KEY_AUTOSAVE, bool(value))

    # --- ツールモード ---

    def mode(self) -> str:
        value = str(self._qsettings.value(_KEY_MODE, DEFAULT_MODE))
        return value if value in ("rect", "pen") else DEFAULT_MODE

    def set_mode(self, value: str) -> None:
        self._qsettings.setValue(_KEY_MODE, value)

    # --- ウィンドウジオメトリ ---

    def geometry(self) -> QByteArray | None:
        value = self._qsettings.value(_KEY_GEOMETRY)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_geometry(self, value: QByteArray) -> None:
        self._qsettings.setValue(_KEY_GEOMETRY, value)
