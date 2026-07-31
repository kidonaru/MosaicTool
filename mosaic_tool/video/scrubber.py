"""スクラブ用: 再生と同じ長寿命パイプからプロキシフレームを 1 枚ずつ取り出す

シークのたびに ffmpeg を起動すると 1 回ごとにプロセス起動とシークデコードの
コスト(100ms 超)が掛かる。再生エンジンと同じくプロキシ解像度の rawvideo を
パイプで受け、前方への移動はパイプの読み飛ばしだけで済ませる。
後方や遠くへの移動だけパイプを張り直す。
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from mosaic_tool.video import ffmpeg as video_ffmpeg
from mosaic_tool.video.ffmpeg import VideoInfo
from mosaic_tool.video.player import READ_CHUNK, STOP_WAIT_MS

# 読み飛ばしで済ませる前方距離の上限 (秒)。パイプの張り直しは 0.2 秒程度、
# 読み飛ばしは実時間の 3 倍速程度なので、それより遠くは張り直した方が早い
SKIP_AHEAD_S = 0.5


class Scrubber(QThread):
    """要求されたフレームをプロキシ解像度の QImage として順に取り出すスレッド

    取り出し中に届いた要求は最新の 1 件だけ残す(FrameFetcher と同じ合流方式)。
    """

    frame_ready = Signal(int, QImage)  # (フレーム番号, プロキシ解像度の画像)
    failed = Signal(int, str)

    def __init__(self, path: Path, info: VideoInfo, parent=None):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._size = video_ffmpeg.proxy_size(info)
        self._cond = threading.Condition()
        self._request: int | None = None
        self._quit = False
        self._proc: subprocess.Popen | None = None
        self._buffer = bytearray()
        self._next = 0  # パイプから次に出てくるフレーム番号

    def request(self, frame: int) -> None:
        """frame の取り出しを頼む(未処理の要求があれば置き換える)"""
        with self._cond:
            self._request = frame
            self._cond.notify()

    def stop(self) -> None:
        """スレッドを終わらせる(読み出し中ならパイプを切って解く)"""
        with self._cond:
            self._quit = True
            proc = self._proc
            self._cond.notify()
        video_ffmpeg.kill_process(proc)
        self.wait(STOP_WAIT_MS)

    def run(self) -> None:
        try:
            while True:
                with self._cond:
                    while self._request is None and not self._quit:
                        self._cond.wait()
                    if self._quit:
                        return
                    frame = self._request
                    self._request = None
                data = self._serve(frame)
                if data is None:
                    continue  # 失敗(通知済み)か、より新しい要求への中断
                with self._cond:
                    # 取り出している間に新しい要求が来ていたら、古い画像は流さない
                    if self._request is not None or self._quit:
                        continue
                width, height = self._size
                image = QImage(
                    data, width, height, width * 3, QImage.Format.Format_RGB888
                ).copy()
                self.frame_ready.emit(frame, image)
        finally:
            self._close()

    # --- パイプの操作(ワーカースレッド内でのみ触る) ---

    def _serve(self, frame: int) -> bytes | None:
        """frame のデータを返す。失敗したら failed を出して None"""
        limit = max(1, int(self._info.fps * SKIP_AHEAD_S))
        proc = self._proc
        reusable = (
            proc is not None
            and proc.poll() is None
            and self._next <= frame <= self._next + limit
        )
        if not reusable and not self._restart(frame):
            return None
        data = None
        while self._next <= frame:
            with self._cond:
                # 新しい要求が来ていたら読み飛ばしを打ち切る(進んだ位置は残る)
                if self._request is not None or self._quit:
                    return None
            data = self._read_frame()
            if data is None:
                self._close()
                # 停止によるパイプ切断も EOF として現れるため、通知しない
                if not self._quit:
                    self.failed.emit(
                        frame, f"フレームを取り出せません (frame {frame})"
                    )
                return None
            self._next += 1
        return data

    def _restart(self, frame: int) -> bool:
        """パイプを frame から張り直す。起動できなければ failed を出して False"""
        self._close()
        self._buffer.clear()
        try:
            proc = self._open(frame)
        except OSError as e:
            self.failed.emit(frame, f"フレームを取り出せません: {e}")
            return False
        with self._cond:
            self._proc = proc
            # stop() が _proc を見た後に起動された場合はここで切る
            if self._quit:
                proc.kill()
        self._next = frame
        return True

    def _open(self, frame: int) -> subprocess.Popen:
        cmd = video_ffmpeg.playback_command(self._path, self._info, frame, self._size)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=video_ffmpeg.subprocess_flags(),
        )

    def _read_frame(self) -> bytes | None:
        """パイプから 1 フレーム読む。EOF なら None"""
        width, height = self._size
        frame_bytes = width * height * 3
        while len(self._buffer) < frame_bytes:
            chunk = self._proc.stdout.read(READ_CHUNK)
            if not chunk:
                return None
            self._buffer.extend(chunk)
        data = bytes(self._buffer[:frame_bytes])
        del self._buffer[:frame_bytes]
        return data

    def _close(self) -> None:
        with self._cond:
            proc, self._proc = self._proc, None
        video_ffmpeg.close_process(proc)
