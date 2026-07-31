"""動画の書き出し: デコード → フレームごとのモザイク合成 → エンコード

ffmpeg のデコーダとエンコーダを 2 プロセス起動し、rawvideo をパイプで
中継しながら 1 フレームずつ合成する。UI を止めないよう QThread で動かす。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPainterPath

from mosaic_tool.mosaic import apply_mosaic
from mosaic_tool.video import ffmpeg
from mosaic_tool.video.ffmpeg import VideoInfo

# フレームを流し終えた後の後始末の待ち時間 (秒)。エンコーダは faststart の
# 書き直しが残っているため長め、デコーダは即終わる想定
DECODER_WAIT = 60
ENCODER_WAIT = 600
KILL_WAIT = 10


class VideoExporter(QThread):
    """動画 1 本の書き出しスレッド

    frame_paths は (開始フレーム, 終了フレーム, 画像座標パス) のリスト。
    QPainterPath はウィジェット非依存のためワーカースレッドから触れる。
    """

    progress = Signal(int, int)          # (処理済みフレーム数, 総フレーム数)
    export_finished = Signal(bool, str)  # (成功か, メッセージ)

    def __init__(
        self,
        src: Path,
        dest: Path,
        info: VideoInfo,
        frame_paths: list[tuple[int, int, QPainterPath]],
        block: int,
        threshold: float,
        strip_meta: bool,
        export: ffmpeg.ExportSettings,
    ):
        super().__init__()
        self._src = src
        self._dest = dest
        self._info = info
        self._frame_paths = frame_paths
        self._block = block
        self._threshold = threshold
        self._strip_meta = strip_meta
        self._export = export
        self._cancelled = False
        self._decoder = None
        self._encoder = None

    def cancel(self) -> None:
        """中断を要求する(メインスレッドから呼ぶ)

        パイプ読み書きでブロックしていても抜けられるよう、プロセスも落とす。
        """
        self._cancelled = True
        self._kill(self._decoder)
        self._kill(self._encoder)

    def _paths_at(self, frame: int) -> list[QPainterPath]:
        return [p for start, end, p in self._frame_paths if start <= frame <= end]

    def _read_frame(self, stdout) -> bytes:
        """rawvideo 1 フレーム分を読み切る。EOF なら空、途切れたら VideoError"""
        size = self._info.width * self._info.height * 3
        chunks = []
        remain = size
        while remain > 0:
            chunk = stdout.read(remain)
            if not chunk:
                break
            chunks.append(chunk)
            remain -= len(chunk)
        data = b"".join(chunks)
        if data and len(data) != size:
            raise ffmpeg.VideoError("デコード出力がフレームの途中で途切れました")
        return data

    def run(self) -> None:
        try:
            decoder = self._decoder = subprocess.Popen(
                ffmpeg.decode_command(self._src, self._info),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=ffmpeg.subprocess_flags(),
            )
            encoder = self._encoder = subprocess.Popen(
                ffmpeg.encode_command(
                    self._src, self._dest, self._info,
                    strip_meta=self._strip_meta, export=self._export,
                ),
                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=ffmpeg.subprocess_flags(),
            )
            size = (self._info.width, self._info.height)
            frame = 0
            while not self._cancelled:
                data = self._read_frame(decoder.stdout)
                if not data:
                    break
                paths = self._paths_at(frame)
                if paths:
                    img = Image.frombytes("RGB", size, data)
                    out = apply_mosaic(img, paths, self._block, self._threshold)
                    data = out.tobytes()
                encoder.stdin.write(data)
                frame += 1
                self.progress.emit(frame, self._info.frame_count)
            encoder.stdin.close()
            if self._cancelled:
                self._finish_cancelled(decoder, encoder)
                return
            decoder.wait(timeout=DECODER_WAIT)
            encoder.wait(timeout=ENCODER_WAIT)
            if decoder.returncode != 0 or encoder.returncode != 0:
                raise ffmpeg.VideoError("ffmpeg の処理がエラーで終了しました")
            self.export_finished.emit(True, f"保存しました: {self._dest}")
        except Exception as e:
            self._kill(self._decoder)
            self._kill(self._encoder)
            self._remove_partial()
            if self._cancelled:
                # 中断でプロセスを落とした際のパイプ切れは失敗ではなく中断として扱う
                self.export_finished.emit(False, "書き出しをキャンセルしました")
            else:
                message = f"書き出しに失敗しました: {e}"
                if self._export.codec == "h265":
                    # エンコーダ不在でも詳細が残らないため、可能性として案内する
                    message += (
                        "\n(ご利用の ffmpeg が H.265 エンコードに"
                        "対応していない可能性があります)"
                    )
                self.export_finished.emit(False, message)
        finally:
            self._kill(self._decoder)
            self._kill(self._encoder)

    def _finish_cancelled(self, decoder, encoder) -> None:
        self._kill(decoder)
        self._kill(encoder)
        self._remove_partial()
        self.export_finished.emit(False, "書き出しをキャンセルしました")

    def _remove_partial(self) -> None:
        """中断・失敗時の書きかけファイルを消す"""
        try:
            self._dest.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _kill(proc) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.kill()
        try:
            proc.wait(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            pass
