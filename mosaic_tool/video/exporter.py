"""動画の書き出し: デコード → フレームごとのモザイク合成 → エンコード

ffmpeg のデコーダとエンコーダを 2 プロセス起動し、rawvideo をパイプで
中継しながら 1 フレームずつ合成する。UI を止めないよう QThread で動かす。
"""
from __future__ import annotations

import subprocess
from heapq import heappop, heappush
from pathlib import Path
from typing import NamedTuple

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


class _Entry(NamedTuple):
    """索引が持つ区間 1 個。order は元のリストでの並び順"""

    start: int
    end: int
    order: int
    path: QPainterPath


class FramePathIndex:
    """フレーム番号から、そのフレームに掛かるパスを引く索引

    自動検出は検出フレームごとに独立した区間を作るため、長尺動画では区間が
    数万個まで増える。全区間の線形走査を全フレームに対して行うと書き出しが
    (総フレーム数 × 区間数) に比例して遅くなる。

    書き出しはフレーム 0 から順に進むので、開始フレーム順に並べた区間を掃引し、
    「いま掛かっている区間」だけを終了フレーム順のヒープで保つ。1 フレーム分の
    処理量は実際に掛かっている区間の数で決まるため、動画全体を覆うような長い
    区間が混ざっても走査量が区間数に引きずられない。
    (開始フレームの二分探索だけでは、長い区間が 1 本あるだけで打ち切りが
    効かなくなり線形走査に戻ってしまう)

    フレームが戻る呼び出しは書き出しでは起きないが、掃引の前提が崩れるため
    その場合は掃引をやり直して正しい結果を返す。
    """

    def __init__(self, frame_paths: list[tuple[int, int, QPainterPath]]):
        self._entries = sorted(
            (
                _Entry(start, end, order, path)
                for order, (start, end, path) in enumerate(frame_paths)
            ),
            key=lambda e: e.start,
        )
        self._rewind()

    def _rewind(self) -> None:
        """掃引を先頭からやり直す"""
        # まだ掃引していない区間の位置
        self._next = 0
        # いま掛かっている区間 (終了フレーム順のヒープ)
        self._active: list[tuple[int, int, QPainterPath]] = []
        self._frame: int | None = None

    def paths_at(self, frame: int) -> list[QPainterPath]:
        if self._frame is not None and frame < self._frame:
            self._rewind()
        self._frame = frame
        entries = self._entries
        while self._next < len(entries) and entries[self._next].start <= frame:
            entry = entries[self._next]
            heappush(self._active, (entry.end, entry.order, entry.path))
            self._next += 1
        # 終了フレーム順なので、掛からなくなった区間は先頭にまとまっている
        while self._active and self._active[0][0] < frame:
            heappop(self._active)
        # 返す順は元のリスト順に戻す
        return [path for _, _, path in sorted(self._active, key=lambda a: a[1])]


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
        self._paths = FramePathIndex(frame_paths)
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
        return self._paths.paths_at(frame)

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
        # kill 直後は Windows でエンコーダが出力ファイルを掴んだままのことがあり、
        # 解放を待たずに unlink すると PermissionError で書きかけが残ってしまう
        encoder = self._encoder
        if encoder is not None:
            try:
                encoder.wait(timeout=KILL_WAIT)
            except subprocess.TimeoutExpired:
                pass
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
