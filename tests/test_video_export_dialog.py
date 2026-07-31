"""動画書き出しダイアログ(初期値の復元・プリセット絞り込み・設定保存)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.settings import AppSettings  # noqa: E402
from mosaic_tool.video import ffmpeg  # noqa: E402
from mosaic_tool.video.export_dialog import ExportDialog  # noqa: E402


def _settings(tmp_path) -> AppSettings:
    path = tmp_path / "mosaic_tool.ini"
    return AppSettings(QSettings(str(path), QSettings.Format.IniFormat))


def _info(w=3840, h=2160):
    return ffmpeg.VideoInfo(w, h, 30.0, "30/1", 90, 3.0, None)


def make_dialog(tmp_path, info=None, settings=None):
    QApplication.instance() or QApplication([])
    return ExportDialog(info or _info(), settings or _settings(tmp_path))


class TestDefaults:
    def test_initial_selection_matches_defaults(self, tmp_path):
        d = make_dialog(tmp_path)
        result = d.export_settings()
        assert result == ffmpeg.ExportSettings()

    def test_restores_saved_settings(self, tmp_path):
        s = _settings(tmp_path)
        s.set_video_codec("h265")
        s.set_video_resolution(720)
        s.set_video_crf("h265", 26)
        d = make_dialog(tmp_path, settings=s)
        assert d.export_settings() == ffmpeg.ExportSettings("h265", 720, 26)


class TestCrfSlider:
    def test_shows_the_slider_value_as_crf(self, tmp_path):
        d = make_dialog(tmp_path)
        assert d._crf.value() == 18
        assert d._crf_label.text() == "CRF 18"
        d._crf.setValue(14)
        assert d._crf_label.text() == "CRF 14"

    def test_range_follows_the_codec(self, tmp_path):
        d = make_dialog(tmp_path)
        assert (d._crf.minimum(), d._crf.maximum()) == (14, 28)
        d._codec.setCurrentIndex(d._codec.findData("h265"))
        assert (d._crf.minimum(), d._crf.maximum()) == (18, 32)
        assert d._crf.value() == 22

    def test_keeps_each_codecs_value_while_switching(self, tmp_path):
        # H.264 側で選んだ値はコーデックを往復しても残る
        d = make_dialog(tmp_path)
        d._crf.setValue(15)
        d._codec.setCurrentIndex(d._codec.findData("h265"))
        d._codec.setCurrentIndex(d._codec.findData("h264"))
        assert d._crf.value() == 15


class TestLosslessCodec:
    def test_rawvideo_is_offered(self, tmp_path):
        d = make_dialog(tmp_path)
        values = [d._codec.itemData(i) for i in range(d._codec.count())]
        assert values == ["h264", "h265", "rawvideo"]

    def test_quality_row_is_hidden_for_rawvideo(self, tmp_path):
        d = make_dialog(tmp_path)
        d._codec.setCurrentIndex(d._codec.findData("rawvideo"))
        assert not d._crf_caption.isVisibleTo(d)
        assert not d._crf.isVisibleTo(d)
        # 圧縮コーデックへ戻せば品質行も戻る
        d._codec.setCurrentIndex(d._codec.findData("h264"))
        assert d._crf_caption.isVisibleTo(d)

    def test_rawvideo_settings_carry_the_default_crf(self, tmp_path):
        # CRF は使わないため、スライダーの残り値ではなく既定値を入れる
        d = make_dialog(tmp_path)
        d._crf.setValue(15)
        d._codec.setCurrentIndex(d._codec.findData("rawvideo"))
        result = d.export_settings()
        assert result.codec == "rawvideo"
        assert result.crf == ffmpeg.crf_default("rawvideo")

    def test_accept_keeps_the_crf_of_compressed_codecs(self, tmp_path):
        s = _settings(tmp_path)
        d = make_dialog(tmp_path, settings=s)
        d._crf.setValue(15)
        d._codec.setCurrentIndex(d._codec.findData("rawvideo"))
        d.accept()
        assert s.video_codec() == "rawvideo"
        assert s.video_crf("h264") == 15

    def test_saved_rawvideo_choice_is_restored(self, tmp_path):
        s = _settings(tmp_path)
        s.set_video_codec("rawvideo")
        d = make_dialog(tmp_path, settings=s)
        assert d.export_settings().codec == "rawvideo"


class TestResolutionPresets:
    def test_hides_presets_at_or_above_the_short_side(self, tmp_path):
        # 1920x1080 (短辺 1080) では 1080p は無意味なので出さない
        d = make_dialog(tmp_path, info=_info(1920, 1080))
        values = [
            d._resolution.itemData(i) for i in range(d._resolution.count())
        ]
        assert values == [0, 720, 480]

    def test_portrait_uses_width_as_short_side(self, tmp_path):
        # 縦動画 1080x1920 の短辺は 1080
        d = make_dialog(tmp_path, info=_info(1080, 1920))
        values = [
            d._resolution.itemData(i) for i in range(d._resolution.count())
        ]
        assert values == [0, 720, 480]

    def test_saved_resolution_missing_from_presets_falls_back(self, tmp_path):
        # 前回 1080p を選んでいても、短辺 720 の動画では選択肢にないため元のサイズへ
        s = _settings(tmp_path)
        s.set_video_resolution(1080)
        d = make_dialog(tmp_path, info=_info(1280, 720), settings=s)
        assert d.export_settings().max_short_side is None


class TestAccept:
    def test_accept_saves_to_settings(self, tmp_path):
        s = _settings(tmp_path)
        d = make_dialog(tmp_path, settings=s)
        d._codec.setCurrentIndex(d._codec.findData("h265"))
        d._crf.setValue(26)
        d.accept()
        assert s.video_codec() == "h265"
        assert s.video_crf("h265") == 26

    def test_accept_saves_the_crf_of_each_codec(self, tmp_path):
        # 選び直した後に別コーデックへ切り替えても、両方の値を保存する
        s = _settings(tmp_path)
        d = make_dialog(tmp_path, settings=s)
        d._crf.setValue(15)
        d._codec.setCurrentIndex(d._codec.findData("h265"))
        d._crf.setValue(26)
        d.accept()
        assert s.video_crf("h264") == 15
        assert s.video_crf("h265") == 26

    def test_accept_keeps_saved_resolution_when_preset_is_hidden(self, tmp_path):
        # 短辺の小さい動画でフォールバック表示になっただけなら、保存済みの
        # 1080p を 0 で上書きしない(次に大きい動画を開いたとき復元するため)
        s = _settings(tmp_path)
        s.set_video_resolution(1080)
        d = make_dialog(tmp_path, info=_info(1280, 720), settings=s)
        d.accept()
        assert s.video_resolution() == 1080

    def test_accept_saves_an_explicit_resolution_choice(self, tmp_path):
        # フォールバック表示でも、ユーザーが明示的に選び直した値は保存する
        s = _settings(tmp_path)
        s.set_video_resolution(1080)
        d = make_dialog(tmp_path, info=_info(1280, 720), settings=s)
        d._resolution.setCurrentIndex(d._resolution.findData(480))
        d.accept()
        assert s.video_resolution() == 480
