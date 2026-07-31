"""動画対応の ffmpeg まわり(判定・probe 解析・コマンド組み立て)の検証"""
import json
from pathlib import Path

import pytest

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

    def test_encode_copies_compatible_audio(self, info):
        cmd = ffmpeg.encode_command(Path("in.mp4"), Path("out.mp4"), info, strip_meta=False)
        assert cmd[cmd.index("-c:a") + 1] == "copy"
        assert cmd[cmd.index("-map_metadata") + 1] == "1"
        assert "1:a" in cmd

    def test_encode_reencodes_incompatible_audio(self, info):
        vorbis = ffmpeg.VideoInfo(1280, 720, 30.0, "30/1", 90, 3.0, "vorbis")
        cmd = ffmpeg.encode_command(Path("in.mp4"), Path("out.mp4"), vorbis, strip_meta=False)
        assert cmd[cmd.index("-c:a") + 1] == "aac"

    def test_encode_without_audio(self, info):
        silent = ffmpeg.VideoInfo(1280, 720, 30.0, "30/1", 90, 3.0, None)
        cmd = ffmpeg.encode_command(Path("in.mp4"), Path("out.mp4"), silent, strip_meta=False)
        assert "-c:a" not in cmd
        assert "1:a" not in cmd

    def test_encode_strip_meta(self, info):
        cmd = ffmpeg.encode_command(Path("in.mp4"), Path("out.mp4"), info, strip_meta=True)
        assert cmd[cmd.index("-map_metadata") + 1] == "-1"
