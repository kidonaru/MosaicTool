"""AppSettings の読み書きテスト(INI ファイルに保存して検証する)"""
from PySide6.QtCore import QSettings

from mosaic_tool.settings import (
    DEFAULT_BLOCK,
    DEFAULT_PEN_WIDTH,
    DEFAULT_THRESHOLD,
    AppSettings,
)


def _settings(tmp_path) -> AppSettings:
    path = tmp_path / "mosaic_tool.ini"
    return AppSettings(QSettings(str(path), QSettings.Format.IniFormat))


def test_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.block(5, 100, 5) == DEFAULT_BLOCK
    assert s.threshold(0, 100) == DEFAULT_THRESHOLD
    assert s.pen_width(5, 200) == DEFAULT_PEN_WIDTH
    assert s.autosave() is True
    assert s.mode() == "pen"
    assert s.geometry() is None


def test_roundtrip(tmp_path):
    s = _settings(tmp_path)
    s.set_block(35)
    s.set_threshold(45)
    s.set_pen_width(60)
    s.set_autosave(False)
    s.set_mode("rect")
    # 同じファイルを読み直しても保持されている
    s2 = _settings(tmp_path)
    assert s2.block(5, 100, 5) == 35
    assert s2.threshold(0, 100) == 45
    assert s2.pen_width(5, 200) == 60
    assert s2.autosave() is False
    assert s2.mode() == "rect"


def test_invalid_values_fall_back_to_default(tmp_path):
    s = _settings(tmp_path)
    s._qsettings.setValue("mosaic/block", "abc")
    s._qsettings.setValue("mosaic/threshold", 150)
    s._qsettings.setValue("tool/pen_width", 9999)
    s._qsettings.setValue("tool/mode", "unknown")
    assert s.block(5, 100, 5) == DEFAULT_BLOCK
    assert s.threshold(0, 100) == DEFAULT_THRESHOLD
    assert s.pen_width(5, 200) == DEFAULT_PEN_WIDTH
    assert s.mode() == "pen"


def test_block_off_step_falls_back_to_default(tmp_path):
    """5px 刻みから外れた値(手動編集など)は既定値へ戻す"""
    s = _settings(tmp_path)
    s._qsettings.setValue("mosaic/block", 7)
    assert s.block(5, 100, 5) == DEFAULT_BLOCK
