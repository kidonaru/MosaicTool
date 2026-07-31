"""動画モードの再生: ffmpeg から連続でフレームを受け取り実時間で表示する

1 フレームごとに ffmpeg を起動する方式では数 fps しか出ないため、再生開始位置から
長寿命のプロセスを 1 本だけ立て、プロキシ解像度の rawvideo をパイプで受け取る。
表示は壁時計に合わせ、遅れたフレームは捨てる(実時間優先)。
"""
from __future__ import annotations

import queue
import subprocess
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QImage

from mosaic_tool.video import ffmpeg as video_ffmpeg
from mosaic_tool.video.ffmpeg import VideoInfo

# 先読みするフレーム数。満杯になれば読み出しが止まり ffmpeg の流量も自然に絞られる。
# 1080p の 1 フレームは約 6MB あるため、メモリを抑えて 0.5 秒分に留める
QUEUE_SIZE = 15
# 目標フレームを見に行く間隔 (ms)
TICK_MS = 10
# パイプから 1 回に読むバイト数
READ_CHUNK = 1 << 16
# キューへの投入待ちの区切り (秒)。停止要求を取りこぼさないために区切って待つ
PUT_TIMEOUT = 0.1
# 停止時にスレッドの終了を待つ上限 (ms)
STOP_WAIT_MS = 2000


def target_frame(
    start: int, elapsed_ms: int, fps: float, speed: float, last: int
) -> int:
    """再生開始から elapsed_ms 経った時点で表示すべきフレーム(末尾でクランプ)"""
    frame = start + int(elapsed_ms / 1000.0 * fps * speed)
    return min(frame, last)


def split_frames(buffer: bytearray, frame_bytes: int) -> list[bytes]:
    """受信バッファから完成したフレームを取り出す(取り出した分は buffer から削る)"""
    frames = []
    while len(buffer) >= frame_bytes:
        frames.append(bytes(buffer[:frame_bytes]))
        del buffer[:frame_bytes]
    return frames


class FrameReader(QThread):
    """ffmpeg を 1 本だけ起動し、rawvideo フレームをキューへ流し込むスレッド"""

    failed = Signal(str)

    def __init__(
        self,
        path: Path,
        info: VideoInfo,
        start: int,
        size: tuple[int, int],
        parent=None,
    ):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._start = start
        self._size = size
        self.queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = False
        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        """読み出しを打ち切る(キューを空にして投入待ちも解く)"""
        self._stop = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def run(self) -> None:
        width, height = self._size
        frame_bytes = width * height * 3
        cmd = video_ffmpeg.playback_command(
            self._path, self._info, self._start, self._size
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=video_ffmpeg.subprocess_flags(),
            )
        except OSError as e:
            self.failed.emit(f"再生を開始できません: {e}")
            return
        self._proc = proc
        buffer = bytearray()
        while not self._stop:
            chunk = proc.stdout.read(READ_CHUNK)
            if not chunk:
                break
            buffer.extend(chunk)
            for data in split_frames(buffer, frame_bytes):
                self._put(data)
                if self._stop:
                    break
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    def _put(self, data: bytes) -> None:
        """キューへ入れる(満杯なら停止要求を見ながら待つ)"""
        while not self._stop:
            try:
                self.queue.put(data, timeout=PUT_TIMEOUT)
                return
            except queue.Full:
                continue


class VideoPlayer(QObject):
    """再生の司令塔。壁時計に合わせてキューからフレームを取り出す"""

    frame_ready = Signal(int, QImage)  # (フレーム番号, プロキシ解像度の画像)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, path: Path, info: VideoInfo, parent=None):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._size = video_ffmpeg.proxy_size(info)
        self._speed = 1.0
        self._start = 0   # 時計の基準フレーム
        self._index = 0   # 直前に表示したフレーム
        self._next = 0    # 次にキューから出てくるフレーム番号
        self._reader: FrameReader | None = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    def is_playing(self) -> bool:
        return self._reader is not None

    def set_speed(self, speed: float) -> None:
        """再生速度を変える(再生中は現在位置から時計を張り直す)"""
        self._speed = speed
        if self.is_playing():
            self._start = self._index
            self._clock.restart()

    def start(self, frame: int) -> None:
        """frame から再生を始める。末尾なら何も再生せず終了を通知する"""
        self.stop()
        last = self._last_frame()
        if frame >= last:
            self.finished.emit()
            return
        self._start = self._index = self._next = frame
        reader = FrameReader(self._path, self._info, frame, self._size, self)
        reader.failed.connect(self.failed)
        self._reader = reader
        reader.start()
        self._clock.start()
        self._timer.start()

    def stop(self) -> None:
        """再生を止める(何も再生していなければ何もしない)"""
        self._timer.stop()
        reader, self._reader = self._reader, None
        if reader is None:
            return
        reader.stop()
        reader.wait(STOP_WAIT_MS)

    def _last_frame(self) -> int:
        return max(0, self._info.frame_count - 1)

    def _on_tick(self) -> None:
        reader = self._reader
        if reader is None:
            return
        last = self._last_frame()
        target = target_frame(
            self._start, self._clock.elapsed(), self._info.fps, self._speed, last
        )
        data = None
        index = self._index
        starved = False
        # 目標フレームまで読み捨て、最後の 1 枚だけ表示する(実時間優先のコマ落ち)
        while self._next <= target:
            try:
                data = reader.queue.get_nowait()
            except queue.Empty:
                starved = True
                break
            index = self._next
            self._next += 1
        if data is None:
            if not reader.isRunning() and reader.queue.empty():
                self.stop()
                self.finished.emit()
                return
            if starved:
                # デコード待ちの間だけ時計を張り直し、復帰時にまとめて
                # コマ落ちしないようにする。表示時刻が来ていないだけの tick で
                # 巻き戻すと時計が進まず再生が止まってしまう
                self._start = self._index
                self._clock.restart()
            return
        self._index = index
        width, height = self._size
        image = QImage(
            data, width, height, width * 3, QImage.Format.Format_RGB888
        ).copy()
        self.frame_ready.emit(index, image)
        if index >= last:
            self.stop()
            self.finished.emit()
