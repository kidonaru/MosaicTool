"""動画対応の ffmpeg まわり: パス解決・probe 解析・コマンド組み立て(GUI 非依存)

ffmpeg / ffprobe は同梱せず、セットアップ時に ffmpeg/ へダウンロードする
(配布物のサイズとライセンスへの影響を避けるため)。置き場は推論ランタイムの
runtime/ とは分ける(理由は ffmpeg_dir を参照)。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mosaic_tool.detect.paths import base_dir

# 対応する動画拡張子
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg"}

FFMPEG_DIR_NAME = "ffmpeg"

# mp4 コンテナへそのままコピーできる音声コーデック。それ以外は AAC へ再エンコードする
MP4_COPY_AUDIO = {"aac", "mp3", "ac3", "eac3"}

# 書き出し設定のプリセット。品質は CRF (小さいほど高品質) をそのまま扱う。
# 同じ見た目の品質でも H.265 は CRF が高めになるため、レンジと既定値はコーデック別
EXPORT_SHORT_SIDES = (1080, 720, 480)  # 解像度プリセット (短辺上限 px)
EXPORT_CRF_RANGES = {"h264": (14, 28), "h265": (18, 32)}  # (高品質, 低容量)
EXPORT_CRF_DEFAULTS = {"h264": 18, "h265": 22}  # h264 の 18 は従来の書き出し品質

# 選べる書き出しコーデック。rawvideo は無圧縮(CRF を持たない)
EXPORT_CODECS = ("h264", "h265", "rawvideo")
DEFAULT_CONTAINER = ".mp4"
# 無圧縮の rawvideo は mp4 に収められないため AVI で出す
CONTAINER_BY_CODEC = {"rawvideo": ".avi"}

# 無圧縮の見積もりに乗せる余裕。映像の生サイズに加えてコンテナのインデックスと
# PCM 音声が載るため、その分を割り増しておく
LOSSLESS_SIZE_MARGIN = 1.05

# 再生プレビューの横幅上限 (px)。1080p 以下は原寸のまま再生し、4K などは
# 縮めてパイプの帯域を抑える(4K 原寸の rawvideo はデコードが実時間に届かない)
PROXY_MAX_WIDTH = 1920

# シークバーのホバープレビュー用サムネイル。全編を THUMBNAIL_COUNT 分割した
# 代表フレームを事前に取り出しておき、ホバー時は最寄りを出すだけにする
THUMBNAIL_COUNT = 100
THUMBNAIL_MAX_WIDTH = 160

# probe は読むだけなので短め、フレーム抽出は後方フレームへのシークを見込んで長め (秒)
PROBE_TIMEOUT = 60
EXTRACT_TIMEOUT = 120


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def container_suffix(codec: str) -> str:
    """コーデックを収める出力コンテナの拡張子(既定は mp4)"""
    return CONTAINER_BY_CODEC.get(codec, DEFAULT_CONTAINER)


def is_lossless(codec: str) -> bool:
    """CRF による品質指定を持たない無圧縮コーデックか"""
    return codec == "rawvideo"


def mc_video_path(src: Path, codec: str = "h264") -> Path:
    """動画の保存先: 同じ場所の 名前_mc.<コンテナ拡張子>"""
    return src.with_name(f"{src.stem}_mc{container_suffix(codec)}")


def _is_windows() -> bool:
    return sys.platform == "win32"


def _exe(name: str) -> str:
    return f"{name}.exe" if _is_windows() else name


def ffmpeg_dir() -> Path:
    """ffmpeg / ffprobe の置き場

    推論環境のセットアップは runtime/ を uv venv --clear で作り直すため、
    その配下に置くと巻き添えで消える。runtime/ と並べて別に持つ。
    """
    return base_dir() / FFMPEG_DIR_NAME


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


def kill_process(proc: subprocess.Popen | None) -> None:
    """起動済みのプロセスを落とす(未起動・終了済みなら何もしない)"""
    if proc is not None and proc.poll() is None:
        proc.kill()


def close_process(proc: subprocess.Popen | None) -> None:
    """パイプを閉じてプロセスを落とし、後始末が終わるまで待つ"""
    if proc is None:
        return
    if proc.stdout is not None:
        proc.stdout.close()
    kill_process(proc)
    proc.wait()


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


@dataclass(frozen=True)
class ExportSettings:
    """書き出しダイアログで選ぶ設定。既定値は従来の書き出し (H.264 / CRF 18 / 元サイズ)"""

    codec: str = "h264"                # EXPORT_CODECS のいずれか
    max_short_side: int | None = None  # 短辺の上限 px。None は元のサイズ
    crf: int = EXPORT_CRF_DEFAULTS["h264"]  # 小さいほど高品質(rawvideo では未使用)


def crf_range(codec: str) -> tuple[int, int]:
    """コーデックで扱う CRF の (最小=高品質, 最大=低容量)

    未知のコーデックは encode_command のフォールバックと揃えて h264 扱いにする。
    """
    return EXPORT_CRF_RANGES.get(codec, EXPORT_CRF_RANGES["h264"])


def crf_default(codec: str) -> int:
    """コーデックごとの既定 CRF (未知のコーデックは h264 扱い)"""
    return EXPORT_CRF_DEFAULTS.get(codec, EXPORT_CRF_DEFAULTS["h264"])


def export_size(info: VideoInfo, max_short_side: int | None) -> tuple[int, int]:
    """書き出しの出力サイズ。短辺を上限に縮小のみ行い、偶数へ丸める

    偶数へ丸めるのは yuv420p が奇数サイズを扱えないため (縮小なしでも適用する)。
    """
    short = min(info.width, info.height)
    if max_short_side is None or short <= max_short_side:
        width, height = info.width, info.height
    else:
        width = round(info.width * max_short_side / short)
        height = round(info.height * max_short_side / short)
    return max(2, width - width % 2), max(2, height - height % 2)


def estimated_output_bytes(info: VideoInfo, export: ExportSettings) -> int | None:
    """書き出しサイズの概算 (bytes)。見積もれないコーデックは None

    無圧縮は「幅 × 高さ × 3 バイト × フレーム数」で決まるため事前に読める。
    圧縮コーデックは中身次第で何倍も変わるため、当てにならない数字は出さない。
    """
    if not is_lossless(export.codec):
        return None
    width, height = export_size(info, export.max_short_side)
    raw = width * height * 3 * info.frame_count
    return int(raw * LOSSLESS_SIZE_MARGIN)


def free_bytes(dest: Path) -> int | None:
    """保存先ドライブの空き容量 (bytes)。取得できなければ None"""
    try:
        return shutil.disk_usage(dest.parent).free
    except OSError:
        return None


def format_size(size: int) -> str:
    """バイト数を GB / MB の読みやすい表記にする"""
    gb = size / 1024 ** 3
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{size / 1024 ** 2:.0f} MB"


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
    src: Path,
    info: VideoInfo,
    step: int,
    out_pattern: str,
    *,
    start: int = 0,
    count: int | None = None,
) -> list[str]:
    """検出用に step フレームおきの JPEG を out_pattern へ書き出すコマンド

    start から count 枚だけ取り出す。連番の k 枚目(1 始まり)は
    正規化後のフレーム start + (k-1) * step に対応する。
    -ss は extract_frame_command と同じく前フレームとの中間時刻を指し、
    丸め誤差で隣のフレームから始まらないようにする。
    """
    filters = _fps_filter(info)
    if step > 1:
        filters += f",select='not(mod(n\\,{step}))'"
    time = max(0.0, (start - 0.5) / info.fps)
    cmd = [
        str(ffmpeg_path()), "-v", "error",
        "-ss", f"{time:.6f}", "-i", str(src),
        "-vf", filters, "-fps_mode", "vfr",
    ]
    if count is not None:
        cmd += ["-frames:v", str(count)]
    cmd += ["-q:v", "2", out_pattern]
    return cmd


def proxy_size(info: VideoInfo, max_width: int = PROXY_MAX_WIDTH) -> tuple[int, int]:
    """再生プレビューの描画サイズ。max_width を上限に縦横比を保った偶数サイズ

    偶数へ丸めるのは scale フィルタと相性を取るため。プロキシはシーン矩形へ
    伸ばして表示するので、1px の切り詰めは表示に影響しない。
    """
    if info.width <= max_width:
        width, height = info.width, info.height
    else:
        width = max_width
        height = max(1, round(info.height * max_width / info.width))
    return max(2, width - width % 2), max(2, height - height % 2)


def thumbnail_step(frame_count: int, count: int = THUMBNAIL_COUNT) -> int:
    """サムネイルの間隔(フレーム数)。全編を count 分割し、短い動画は全フレーム"""
    return max(1, frame_count // count)


def thumbnails_command(
    src: Path, info: VideoInfo, step: int, size: tuple[int, int]
) -> list[str]:
    """step フレームおきのサムネイルを rawvideo (RGB24) として標準出力へ流すコマンド

    1 パスで全編から取り出す。1 フレームは幅 × 高さ × 3 バイト固定なので、
    読み出し側は k 枚目をフレーム k * step に対応付けるだけでよい。
    """
    filters = _fps_filter(info)
    if step > 1:
        filters += f",select='not(mod(n\\,{step}))'"
    width, height = size
    filters += f",scale={width}:{height}"
    return [
        str(ffmpeg_path()), "-v", "error",
        "-i", str(src),
        "-vf", filters, "-fps_mode", "vfr",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def playback_command(
    src: Path, info: VideoInfo, start: int, size: tuple[int, int]
) -> list[str]:
    """再生用に start 以降を rawvideo (RGB24) として標準出力へ流し続けるコマンド

    1 フレームは幅 × 高さ × 3 バイト固定なので、読み出し側はフレーム境界を
    解析せずに切り出せる。
    """
    time = max(0.0, (start - 0.5) / info.fps)
    width, height = size
    return [
        str(ffmpeg_path()), "-v", "error",
        "-ss", f"{time:.6f}", "-i", str(src),
        "-vf", f"{_fps_filter(info)},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def decode_command(src: Path, info: VideoInfo) -> list[str]:
    """全フレームを rawvideo (RGB24) として標準出力へ流すコマンド(書き出し用)"""
    return [
        str(ffmpeg_path()), "-v", "error",
        "-i", str(src), "-vf", _fps_filter(info),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def encode_command(
    src: Path, dest: Path, info: VideoInfo, *, strip_meta: bool, export: ExportSettings
) -> list[str]:
    """標準入力の rawvideo をエンコードし、音声を元動画から引き継ぐコマンド"""
    cmd = [
        str(ffmpeg_path()), "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{info.width}x{info.height}", "-r", info.fps_expr, "-i", "-",
        "-i", str(src),
        "-map", "0:v",
    ]
    if info.audio_codec is not None:
        cmd += ["-map", "1:a"]
    width, height = export_size(info, export.max_short_side)
    lossless = is_lossless(export.codec)
    if lossless:
        # 無圧縮 (AVI)。入力の RGB をそのまま格納するため CRF は使わない
        cmd += ["-c:v", "rawvideo", "-pix_fmt", "bgr24"]
    else:
        encoder = "libx265" if export.codec == "h265" else "libx264"
        cmd += ["-c:v", encoder, "-pix_fmt", "yuv420p", "-crf", str(export.crf)]
    cmd += ["-vf", f"scale={width}:{height}"]
    if export.codec == "h265":
        # hvc1: Apple 系プレイヤーで再生できるようにするタグ。
        # preset fast: x265 の既定 (medium) は極端に遅く、エンコーダ待ちの
        # タイムアウト(exporter.ENCODER_WAIT)に届きうるため速度優先にする
        cmd += ["-tag:v", "hvc1", "-preset", "fast"]
    if info.audio_codec is not None:
        if lossless:
            # AVI は AAC を収めにくいため、音声も無圧縮の PCM にする
            cmd += ["-c:a", "pcm_s16le"]
        elif info.audio_codec in MP4_COPY_AUDIO:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
    # 既定でもグローバルメタデータは 2 入力目から引き継がれないため明示する
    cmd += ["-map_metadata", "-1" if strip_meta else "1"]
    if not lossless:
        # faststart は mp4 コンテナ専用のオプション
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(dest))
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
