"""AppSettings の読み書きテスト(INI ファイルに保存して検証する)"""
from PySide6.QtCore import QSettings

from mosaic_tool.settings import (
    DEFAULT_BLOCK,
    DEFAULT_CONFIDENCE,
    DEFAULT_DEVICE,
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


def test_model_settings_default_to_enabled_with_given_default(tmp_path):
    s = _settings(tmp_path)
    # 未登録のモデルは「有効・呼び出し側の既定値」として扱う
    assert s.model_enabled("Anzhc Eyes -seg-hd.pt") is True
    assert s.model_confidence("Anzhc Eyes -seg-hd.pt", 1, 100, 40) == 40


def test_model_confidence_falls_back_to_the_shared_default(tmp_path):
    s = _settings(tmp_path)
    assert s.model_confidence("unknown.pt", 1, 100) == DEFAULT_CONFIDENCE


def test_model_settings_roundtrip(tmp_path):
    name = "Anzhc Face seg 640 v4 y11n.pt"
    s = _settings(tmp_path)
    s.set_model_enabled(name, False)
    s.set_model_confidence(name, 33)
    s2 = _settings(tmp_path)
    assert s2.model_enabled(name) is False
    assert s2.model_confidence(name, 1, 100, 25) == 33


def test_model_settings_are_kept_per_file(tmp_path):
    s = _settings(tmp_path)
    s.set_model_enabled("a.pt", False)
    assert s.model_enabled("b.pt") is True


def test_model_confidence_out_of_range_falls_back_to_default(tmp_path):
    name = "a.pt"
    s = _settings(tmp_path)
    s.set_model_confidence(name, 500)
    assert s.model_confidence(name, 1, 100, 25) == 25


def test_model_classes_defaults_to_empty(tmp_path):
    # 未設定は「制限なし(全クラス)」を意味する空リスト
    s = _settings(tmp_path)
    assert s.model_classes("m.pt") == []


def test_model_classes_roundtrip(tmp_path):
    s = _settings(tmp_path)
    s.set_model_classes("m.pt", ["penis", "pussy"])
    assert _settings(tmp_path).model_classes("m.pt") == ["penis", "pussy"]


def test_model_classes_survives_a_single_entry(tmp_path):
    # QSettings は 1 件のリストを文字列で返すことがある
    s = _settings(tmp_path)
    s.set_model_classes("m.pt", ["penis"])
    assert _settings(tmp_path).model_classes("m.pt") == ["penis"]


def test_model_classes_are_per_model(tmp_path):
    s = _settings(tmp_path)
    s.set_model_classes("a.pt", ["penis"])
    assert s.model_classes("b.pt") == []


def test_device_defaults_to_auto(tmp_path):
    assert _settings(tmp_path).device() == DEFAULT_DEVICE


def test_device_roundtrip_and_invalid_value(tmp_path):
    settings = _settings(tmp_path)
    settings.set_device("cpu")
    assert settings.device() == "cpu"
    settings.set_device("gpu")
    assert settings.device() == DEFAULT_DEVICE


def test_video_export_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.video_codec() == "h264"
    assert s.video_resolution() == 0
    # CRF の既定値はコーデックごとに違う
    assert s.video_crf("h264") == 18
    assert s.video_crf("h265") == 22


def test_video_export_roundtrip(tmp_path):
    s = _settings(tmp_path)
    s.set_video_codec("h265")
    s.set_video_resolution(720)
    s.set_video_crf("h264", 20)
    s.set_video_crf("h265", 26)
    s2 = _settings(tmp_path)
    assert s2.video_codec() == "h265"
    assert s2.video_resolution() == 720
    assert s2.video_crf("h264") == 20
    assert s2.video_crf("h265") == 26


def test_video_export_invalid_values_fall_back(tmp_path):
    """手動編集などで壊れた値は既定値へ戻す"""
    s = _settings(tmp_path)
    s._qsettings.setValue("video/codec", "av1")
    s._qsettings.setValue("video/resolution", 999)
    s._qsettings.setValue("video/crf/h264", 50)
    assert s.video_codec() == "h264"
    assert s.video_resolution() == 0
    assert s.video_crf("h264") == 18


def test_video_crf_rejects_the_other_codecs_range(tmp_path):
    """H.265 のレンジ (18〜32) の値は H.264 側では既定値へ戻す"""
    s = _settings(tmp_path)
    s.set_video_crf("h264", 32)
    assert s.video_crf("h264") == 18
