"""動画対応の ffmpeg まわり(判定・probe 解析・コマンド組み立て)の検証"""
import json
from pathlib import Path

import pytest

from mosaic_tool.detect import paths
from mosaic_tool.video import ffmpeg


def _probe_json(video=None, audio=None, fmt=None):
    streams = []
    if video is not None:
        streams.append({"codec_type": "video", **video})
    if audio is not None:
        streams.append({"codec_type": "audio", **audio})
    return json.dumps({"streams": streams, "format": fmt or {}})


VIDEO_STREAM = {
    "width": 1920,
    "height": 1080,
    "avg_frame_rate": "30000/1001",
    "nb_frames": "300",
    "duration": "10.01",
}


class TestIsVideoFile:
    def test_video_extensions(self):
        assert ffmpeg.is_video_file(Path("a.mp4"))
        assert ffmpeg.is_video_file(Path("a.MOV"))
        assert ffmpeg.is_video_file(Path("a.mkv"))

    def test_non_video(self):
        assert not ffmpeg.is_video_file(Path("a.png"))
        assert not ffmpeg.is_video_file(Path("a.txt"))


def test_mc_video_path_is_mp4():
    assert ffmpeg.mc_video_path(Path("C:/x/movie.mkv")) == Path("C:/x/movie_mc.mp4")


class TestParseProbe:
    def test_basic(self):
        info = ffmpeg.parse_probe(
            _probe_json(VIDEO_STREAM, {"codec_name": "aac"})
        )
        assert (info.width, info.height) == (1920, 1080)
        assert info.fps == pytest.approx(29.97, abs=0.01)
        assert info.fps_expr == "30000/1001"
        assert info.frame_count == 300
        assert info.audio_codec == "aac"

    def test_no_audio(self):
        info = ffmpeg.parse_probe(_probe_json(VIDEO_STREAM))
        assert info.audio_codec is None

    def test_frame_count_fallback_from_duration(self):
        video = dict(VIDEO_STREAM, nb_frames=None, avg_frame_rate="30/1")
        info = ffmpeg.parse_probe(_probe_json(video, fmt={"duration": "10.0"}))
        assert info.frame_count == 300

    def test_fps_fallback_to_r_frame_rate(self):
        video = dict(VIDEO_STREAM, avg_frame_rate="0/0", r_frame_rate="24/1")
        info = ffmpeg.parse_probe(_probe_json(video))
        assert info.fps == 24.0

    def test_no_video_stream(self):
        with pytest.raises(ffmpeg.VideoError):
            ffmpeg.parse_probe(_probe_json(audio={"codec_name": "aac"}))

    def test_broken_json(self):
        with pytest.raises(ffmpeg.VideoError):
            ffmpeg.parse_probe("not json")

    def test_missing_size(self):
        video = dict(VIDEO_STREAM, width=0)
        with pytest.raises(ffmpeg.VideoError):
            ffmpeg.parse_probe(_probe_json(video))


@pytest.fixture
def info():
    return ffmpeg.VideoInfo(1280, 720, 30.0, "30/1", 90, 3.0, "aac")


class TestCommands:
    def test_extract_frame_seeks_between_frames(self, info):
        cmd = ffmpeg.extract_frame_command(Path("in.mp4"), 30, info)
        # 丸め誤差で隣のフレームを拾わないよう、半フレーム手前へシークする
        assert cmd[cmd.index("-ss") + 1] == f"{29.5 / 30:.6f}"
        assert "fps=30/1" in cmd

    def test_extract_frame_zero_clamped(self, info):
        cmd = ffmpeg.extract_frame_command(Path("in.mp4"), 0, info)
        assert cmd[cmd.index("-ss") + 1] == "0.000000"

    def test_extract_frames_with_step(self, info):
        cmd = ffmpeg.extract_frames_command(Path("in.mp4"), info, 5, "out_%06d.jpg")
        vf = cmd[cmd.index("-vf") + 1]
        assert "select='not(mod(n\\,5))'" in vf

    def test_extract_frames_without_step(self, info):
        cmd = ffmpeg.extract_frames_command(Path("in.mp4"), info, 1, "out_%06d.jpg")
        vf = cmd[cmd.index("-vf") + 1]
        assert "select" not in vf

    def test_extract_frames_seeks_to_the_start_frame(self, info):
        # 前フレームとの中間時刻へシークする(extract_frame_command と同じ方式)
        cmd = ffmpeg.extract_frames_command(
            Path("in.mp4"), info, 1, "out_%06d.jpg", start=60
        )
        assert cmd.index("-ss") < cmd.index("-i")
        assert float(cmd[cmd.index("-ss") + 1]) == pytest.approx(59.5 / info.fps)

    def test_extract_frames_limits_the_count(self, info):
        cmd = ffmpeg.extract_frames_command(
            Path("in.mp4"), info, 5, "out_%06d.jpg", start=10, count=7
        )
        assert cmd[cmd.index("-frames:v") + 1] == "7"

    def test_extract_frames_without_count_has_no_limit(self, info):
        cmd = ffmpeg.extract_frames_command(Path("in.mp4"), info, 1, "out_%06d.jpg")
        assert "-frames:v" not in cmd

    def test_extract_frames_from_the_head_seeks_to_zero(self, info):
        cmd = ffmpeg.extract_frames_command(Path("in.mp4"), info, 1, "out_%06d.jpg")
        assert float(cmd[cmd.index("-ss") + 1]) == 0.0

    def test_playback_command_streams_rawvideo_from_the_start_frame(self):
        info = ffmpeg.VideoInfo(1920, 1080, 30.0, "30/1", 900, 30.0, None)
        cmd = ffmpeg.playback_command(Path("in.mp4"), info, 300, (960, 540))
        assert cmd.index("-ss") < cmd.index("-i")
        assert float(cmd[cmd.index("-ss") + 1]) == pytest.approx(299.5 / 30.0)
        assert "scale=960:540" in cmd[cmd.index("-vf") + 1]
        assert cmd[cmd.index("-f") + 1] == "rawvideo"
        assert cmd[cmd.index("-pix_fmt") + 1] == "rgb24"
        assert cmd[-1] == "-"

    def test_proxy_size_keeps_small_video_as_is(self):
        info = ffmpeg.VideoInfo(640, 480, 30.0, "30/1", 90, 3.0, None)
        assert ffmpeg.proxy_size(info) == (640, 480)

    def test_proxy_size_shrinks_to_the_max_width(self):
        info = ffmpeg.VideoInfo(1920, 1080, 30.0, "30/1", 90, 3.0, None)
        assert ffmpeg.proxy_size(info, 960) == (960, 540)

    def test_proxy_size_is_even(self):
        # 奇数サイズは 1px 切り詰める(プロキシは伸ばして表示するため影響しない)
        info = ffmpeg.VideoInfo(101, 57, 30.0, "30/1", 90, 3.0, None)
        assert ffmpeg.proxy_size(info, 960) == (100, 56)

    def test_encode_copies_compatible_audio(self, info):
        cmd = ffmpeg.encode_command(
            Path("in.mp4"), Path("out.mp4"), info,
            strip_meta=False, export=ffmpeg.ExportSettings(),
        )
        assert cmd[cmd.index("-c:a") + 1] == "copy"
        assert cmd[cmd.index("-map_metadata") + 1] == "1"
        assert "1:a" in cmd

    def test_encode_reencodes_incompatible_audio(self, info):
        vorbis = ffmpeg.VideoInfo(1280, 720, 30.0, "30/1", 90, 3.0, "vorbis")
        cmd = ffmpeg.encode_command(
            Path("in.mp4"), Path("out.mp4"), vorbis,
            strip_meta=False, export=ffmpeg.ExportSettings(),
        )
        assert cmd[cmd.index("-c:a") + 1] == "aac"

    def test_encode_without_audio(self, info):
        silent = ffmpeg.VideoInfo(1280, 720, 30.0, "30/1", 90, 3.0, None)
        cmd = ffmpeg.encode_command(
            Path("in.mp4"), Path("out.mp4"), silent,
            strip_meta=False, export=ffmpeg.ExportSettings(),
        )
        assert "-c:a" not in cmd
        assert "1:a" not in cmd

    def test_encode_strip_meta(self, info):
        cmd = ffmpeg.encode_command(
            Path("in.mp4"), Path("out.mp4"), info,
            strip_meta=True, export=ffmpeg.ExportSettings(),
        )
        assert cmd[cmd.index("-map_metadata") + 1] == "-1"


class TestCrfPresets:
    def test_ranges_per_codec(self):
        # H.265 は同品質の CRF が高めのためレンジをずらす
        assert ffmpeg.crf_range("h264") == (14, 28)
        assert ffmpeg.crf_range("h265") == (18, 32)

    def test_default_keeps_current_h264_crf(self):
        # H.264 の既定値は従来の書き出し品質 (CRF 18) と一致させる
        assert ffmpeg.crf_default("h264") == 18
        assert ffmpeg.crf_default("h265") == 22

    def test_unknown_codec_falls_back_to_h264(self):
        # encode_command のコーデック選択と同じく未知値は h264 扱いにする
        assert ffmpeg.crf_range("av1") == ffmpeg.crf_range("h264")
        assert ffmpeg.crf_default("av1") == 18


class TestExportSize:
    def _info(self, w, h):
        return ffmpeg.VideoInfo(w, h, 30.0, "30/1", 90, 3.0, None)

    def test_no_limit_keeps_original(self):
        assert ffmpeg.export_size(self._info(1920, 1080), None) == (1920, 1080)

    def test_shrinks_by_short_side(self):
        # 4K → 1080p は短辺 2160 → 1080 の半分に縮む
        assert ffmpeg.export_size(self._info(3840, 2160), 1080) == (1920, 1080)

    def test_portrait_uses_short_side(self):
        # 縦動画の短辺は横幅。1080p 指定でも短辺 1080 なら無変換
        assert ffmpeg.export_size(self._info(1080, 1920), 1080) == (1080, 1920)

    def test_never_upscales(self):
        assert ffmpeg.export_size(self._info(640, 360), 1080) == (640, 360)

    def test_rounds_to_even(self):
        # yuv420p が奇数サイズを扱えないため偶数へ切り詰める
        assert ffmpeg.export_size(self._info(101, 57), None) == (100, 56)


class TestEncodeExportSettings:
    def _cmd(self, info, export):
        return ffmpeg.encode_command(
            Path("in.mp4"), Path("out.mp4"), info, strip_meta=False, export=export
        )

    def test_h264_defaults(self, info):
        cmd = self._cmd(info, ffmpeg.ExportSettings())
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "18"
        assert "-tag:v" not in cmd
        assert "-preset" not in cmd

    def test_h265_uses_libx265_with_hvc1_tag(self, info):
        # hvc1 タグは Apple 系プレイヤーの互換性のため
        cmd = self._cmd(info, ffmpeg.ExportSettings(codec="h265", crf=22))
        assert cmd[cmd.index("-c:v") + 1] == "libx265"
        assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
        assert cmd[cmd.index("-crf") + 1] == "22"
        # x265 の既定 preset (medium) は極端に遅く、エンコーダ待ちの
        # タイムアウト(ENCODER_WAIT)に届きうるため速度優先にする
        assert cmd[cmd.index("-preset") + 1] == "fast"

    def test_scale_uses_computed_size(self):
        uhd = ffmpeg.VideoInfo(3840, 2160, 30.0, "30/1", 90, 3.0, None)
        cmd = self._cmd(uhd, ffmpeg.ExportSettings(max_short_side=1080))
        assert cmd[cmd.index("-vf") + 1] == "scale=1920:1080"

    def test_original_size_still_rounds_to_even(self):
        odd = ffmpeg.VideoInfo(101, 57, 30.0, "30/1", 90, 3.0, None)
        cmd = self._cmd(odd, ffmpeg.ExportSettings())
        assert cmd[cmd.index("-vf") + 1] == "scale=100:56"


class TestFfmpegDir:
    def test_is_outside_the_inference_runtime(self, tmp_path, monkeypatch):
        """推論環境のセットアップ(uv venv --clear)で消えない場所に置くこと"""
        monkeypatch.setattr("mosaic_tool.video.ffmpeg.base_dir", lambda: tmp_path)
        monkeypatch.setattr("mosaic_tool.detect.paths.base_dir", lambda: tmp_path)
        assert paths.runtime_dir() not in ffmpeg.ffmpeg_dir().parents
