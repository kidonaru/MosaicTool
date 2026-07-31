"""動画対応の ffmpeg まわり: パス解決・probe 解析・コマンド組み立て(GUI 非依存)

ffmpeg / ffprobe は同梱せず、セットアップ時に runtime/ffmpeg/ へダウンロードする
(推論ランタイムと同じ方針。配布物のサイズとライセンスへの影響を避けるため)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mosaic_tool.detect.paths import runtime_dir

# 対応する動画拡張子
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg"}

FFMPEG_DIR_NAME = "ffmpeg"

# mp4 コンテナへそのままコピーできる音声コーデック。それ以外は AAC へ再エンコードする
MP4_COPY_AUDIO = {"aac", "mp3", "ac3", "eac3"}

# 書き出しの H.264 品質 (CRF。小さいほど高品質)
ENCODE_CRF = 18

# probe は読むだけなので短め、フレーム抽出は後方フレームへのシークを見込んで長め (秒)
PROBE_TIMEOUT = 60
EXTRACT_TIMEOUT = 120


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def mc_video_path(src: Path) -> Path:
    """動画の保存先: 同じ場所の 名前_mc.mp4(出力コンテナは mp4 に統一)"""
    return src.with_name(f"{src.stem}_mc.mp4")


def _is_windows() -> bool:
    return sys.platform == "win32"


def _exe(name: str) -> str:
    return f"{name}.exe" if _is_windows() else name


def ffmpeg_dir() -> Path:
    return runtime_dir() / FFMPEG_DIR_NAME


def ffmpeg_path() -> Path:
    return ffmpeg_dir() / _exe("ffmpeg")


def ffprobe_path() -> Path:
    return ffmpeg_dir() / _exe("ffprobe")


def is_ffmpeg_ready() -> bool:
    """動画の読み書き環境(ffmpeg / ffprobe)が用意済みか"""
    return ffmpeg_path().is_file() and ffprobe_path().is_file()


def subprocess_flags() -> int:
    """Windows でコンソールウィンドウを出さないためのフラグ"""
    if _is_windows():
        return subprocess.CREATE_NO_WINDOW
    return 0


@dataclass(frozen=True)
class VideoInfo:
    """probe で得た動画の基本情報。fps_expr はフィルタへ渡す分数表記("30000/1001" 等)"""

    width: int
    height: int
    fps: float
    fps_expr: str
    frame_count: int
    duration: float
    audio_codec: str | None


class VideoError(Exception):
    """動画の解析・読み書きに失敗したことを表す"""


def _parse_rate(text: str) -> float:
    """"30000/1001" 形式のフレームレートを float にする。無効なら 0"""
    try:
        num, _, den = text.partition("/")
        n = float(num)
        d = float(den) if den else 1.0
        return n / d if d else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_probe(text: str) -> VideoInfo:
    """ffprobe の JSON 出力を VideoInfo に変換する。解釈できなければ VideoError"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise VideoError(f"動画情報を解釈できません: {e}") from e
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoError("動画ストリームが見つかりません")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise VideoError("動画のサイズを取得できません")
    # 可変フレームレートでも平均値で固定化して扱う(fps フィルタで CFR に正規化する)
    fps_expr = str(video.get("avg_frame_rate") or "")
    fps = _parse_rate(fps_expr)
    if fps <= 0:
        fps_expr = str(video.get("r_frame_rate") or "")
        fps = _parse_rate(fps_expr)
    if fps <= 0:
        raise VideoError("フレームレートを取得できません")
    duration = 0.0
    for source in (video, data.get("format") or {}):
        try:
            duration = float(source.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            break
    try:
        frame_count = int(video.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frame_count = 0
    if frame_count <= 0:
        frame_count = round(duration * fps)
    if frame_count <= 0:
        raise VideoError("フレーム数を取得できません")
    codec = audio.get("codec_name") if audio else None
    return VideoInfo(width, height, fps, fps_expr, frame_count, duration, codec)


# --- コマンド組み立て ---


def probe_command(src: Path) -> list[str]:
    return [
        str(ffprobe_path()), "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams", str(src),
    ]


def _fps_filter(info: VideoInfo) -> str:
    return f"fps={info.fps_expr}"


def extract_frame_command(src: Path, index: int, info: VideoInfo) -> list[str]:
    """指定フレーム 1 枚を PNG として標準出力へ取り出すコマンド

    -ss は前フレームとの中間時刻を指すようにし、丸め誤差で隣のフレームを
    拾わないようにする。
    """
    time = max(0.0, (index - 0.5) / info.fps)
    return [
        str(ffmpeg_path()), "-v", "error",
        "-ss", f"{time:.6f}", "-i", str(src),
        "-vf", _fps_filter(info), "-frames:v", "1",
        "-f", "image2pipe", "-c:v", "png", "-",
    ]


def extract_frames_command(
    src: Path, info: VideoInfo, step: int, out_pattern: str
) -> list[str]:
    """検出用に step フレームおきの JPEG を out_pattern へ書き出すコマンド

    連番の k 枚目(1 始まり)は正規化後のフレーム (k-1) * step に対応する。
    """
    filters = _fps_filter(info)
    if step > 1:
        filters += f",select='not(mod(n\\,{step}))'"
    return [
        str(ffmpeg_path()), "-v", "error",
        "-i", str(src), "-vf", filters, "-fps_mode", "vfr",
        "-q:v", "2", out_pattern,
    ]


def decode_command(src: Path, info: VideoInfo) -> list[str]:
    """全フレームを rawvideo (RGB24) として標準出力へ流すコマンド(書き出し用)"""
    return [
        str(ffmpeg_path()), "-v", "error",
        "-i", str(src), "-vf", _fps_filter(info),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def encode_command(
    src: Path, dest: Path, info: VideoInfo, *, strip_meta: bool
) -> list[str]:
    """標準入力の rawvideo を H.264 でエンコードし、音声を元動画から引き継ぐコマンド"""
    cmd = [
        str(ffmpeg_path()), "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{info.width}x{info.height}", "-r", info.fps_expr, "-i", "-",
        "-i", str(src),
        "-map", "0:v",
    ]
    if info.audio_codec is not None:
        cmd += ["-map", "1:a"]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(ENCODE_CRF),
        # yuv420p は奇数サイズを扱えないため、偶数サイズへ切り詰める
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ]
    if info.audio_codec is not None:
        if info.audio_codec in MP4_COPY_AUDIO:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
    # 既定でもグローバルメタデータは 2 入力目から引き継がれないため明示する
    cmd += ["-map_metadata", "-1" if strip_meta else "1"]
    cmd += ["-movflags", "+faststart", str(dest)]
    return cmd


# --- 実行 ---


def probe(src: Path) -> VideoInfo:
    """動画を解析して VideoInfo を返す。失敗したら VideoError"""
    try:
        result = subprocess.run(
            probe_command(src), capture_output=True,
            creationflags=subprocess_flags(), timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VideoError(f"ffprobe を実行できません: {e}") from e
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise VideoError(f"動画を解析できません: {src}\n{detail}")
    return parse_probe(result.stdout.decode("utf-8", errors="replace"))


def extract_frame(src: Path, index: int, info: VideoInfo) -> bytes:
    """指定フレームを PNG バイト列として取り出す。失敗したら VideoError"""
    try:
        result = subprocess.run(
            extract_frame_command(src, index, info), capture_output=True,
            creationflags=subprocess_flags(), timeout=EXTRACT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VideoError(f"ffmpeg を実行できません: {e}") from e
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise VideoError(f"フレームを取り出せません (frame {index})\n{detail}")
    return result.stdout
