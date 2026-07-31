"""シークバーのホバープレビュー用サムネイルをバックグラウンドで生成する

ホバー位置のフレームをその場で取り出すとパイプの張り直し(0.2 秒程度)が
ホバーのたびに走って表示が待たされる。代わりに動画を開いた直後から 1 パスで
全編の代表フレームを取り出してメモリへ貯め、ホバー時は最寄りを出すだけにする。
できた分から順に流すので、生成が終わる前でも部分的にプレビューできる。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from mosaic_tool.video import ffmpeg as video_ffmpeg
from mosaic_tool.video.ffmpeg import VideoInfo
from mosaic_tool.video.player import READ_CHUNK, STOP_WAIT_MS


class Thumbnailer(QThread):
    """全編のサムネイルを 1 パスで取り出して順に流すスレッド"""

    thumb_ready = Signal(int, QImage)  # (フレーム番号, サムネイル画像)
    failed = Signal(str)

    def __init__(self, path: Path, info: VideoInfo, parent=None):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._step = video_ffmpeg.thumbnail_step(info.frame_count)
        self._size = video_ffmpeg.proxy_size(
            info, video_ffmpeg.THUMBNAIL_MAX_WIDTH
        )
        self._proc: subprocess.Popen | None = None
        self._quit = False

    def stop(self) -> None:
        """スレッドを終わらせる(読み出し中ならパイプを切って解く)"""
        self._quit = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        self.wait(STOP_WAIT_MS)

    def run(self) -> None:
        try:
            proc = self._open()
        except OSError as e:
            self.failed.emit(f"サムネイルを生成できません: {e}")
            return
        self._proc = proc
        # stop() が _proc を見た後に起動された場合はここで切る
        if self._quit:
            proc.kill()
        try:
            width, height = self._size
            frame_bytes = width * height * 3
            buffer = bytearray()
            index = 0
            while not self._quit:
                chunk = proc.stdout.read(READ_CHUNK)
                if not chunk:
                    break  # EOF(全編を読み終えたか、停止でパイプが切れた)
                buffer.extend(chunk)
                while len(buffer) >= frame_bytes:
                    data = bytes(buffer[:frame_bytes])
                    del buffer[:frame_bytes]
                    image = QImage(
                        data, width, height, width * 3,
                        QImage.Format.Format_RGB888,
                    ).copy()
                    if self._quit:
                        return
                    self.thumb_ready.emit(index * self._step, image)
                    index += 1
        finally:
            self._close()

    def _open(self) -> subprocess.Popen:
        cmd = video_ffmpeg.thumbnails_command(
            self._path, self._info, self._step, self._size
        )
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=video_ffmpeg.subprocess_flags(),
        )

    def _close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
