"""サムネイル生成(1 パスの rawvideo 読み出し)の検証"""
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
from mosaic_tool.video.thumbnailer import Thumbnailer  # noqa: E402

# proxy_size(INFO, 160) == (64, 48)、thumbnail_step(300) == 3 になる小さな動画
INFO = VideoInfo(64, 48, 30.0, "30/1", 300, 10.0, None)
THUMB_BYTES = 64 * 48 * 3

WAIT_S = 5.0


class FakeProc:
    """サムネイル k を k % 256 で埋めて順に流す ffmpeg プロセスのふり"""

    def __init__(self, count: int):
        self._chunks = b"".join(
            bytes([k % 256]) * THUMB_BYTES for k in range(count)
        )
        self._pos = 0
        self.killed = False
        self.stdout = self

    def read(self, n: int) -> bytes:
        if self.killed:
            return b""
        chunk = self._chunks[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass

    def poll(self):
        return 0 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, *args) -> int:
        return 0


class EndlessProc(FakeProc):
    """kill されるまでフレームを流し続けるプロセスのふり"""

    def __init__(self):
        super().__init__(count=0)

    def read(self, n: int) -> bytes:
        if self.killed:
            return b""
        return b"\0" * n


def make_thumbnailer(proc):
    QApplication.instance() or QApplication([])

    class FakeThumbnailer(Thumbnailer):
        def _open(self):
            return proc

    return FakeThumbnailer(Path("movie.mp4"), INFO)


def wait_until(cond):
    deadline = time.monotonic() + WAIT_S
    while not cond():
        assert time.monotonic() < deadline, "時間内に終わりませんでした"
        time.sleep(0.005)


class TestThumbnailer:
    def test_step_and_size_follow_the_video(self):
        thumbnailer = make_thumbnailer(FakeProc(0))
        assert thumbnailer._step == 3
        assert thumbnailer._size == (64, 48)

    def test_all_thumbnails_are_emitted_with_their_frame_numbers(self):
        thumbnailer = make_thumbnailer(FakeProc(100))
        results = []
        thumbnailer.thumb_ready.connect(
            lambda f, img: results.append((f, img)),
            Qt.ConnectionType.DirectConnection,
        )
        thumbnailer.start()
        try:
            wait_until(thumbnailer.isFinished)
        finally:
            thumbnailer.stop()
        assert [f for f, _ in results] == [k * 3 for k in range(100)]
        frame, image = results[7]
        assert frame == 21
        assert (image.width(), image.height()) == (64, 48)

    def test_stop_kills_the_stream(self):
        proc = EndlessProc()
        thumbnailer = make_thumbnailer(proc)
        thumbnailer.start()
        wait_until(thumbnailer.isRunning)
        thumbnailer.stop()
        assert proc.killed
        assert not thumbnailer.isRunning()

    def test_open_failure_is_reported(self):
        QApplication.instance() or QApplication([])

        class BrokenThumbnailer(Thumbnailer):
            def _open(self):
                raise OSError("ffmpeg がありません")

        thumbnailer = BrokenThumbnailer(Path("movie.mp4"), INFO)
        failures = []
        thumbnailer.failed.connect(
            failures.append, Qt.ConnectionType.DirectConnection
        )
        thumbnailer.start()
        try:
            wait_until(thumbnailer.isFinished)
        finally:
            thumbnailer.stop()
        assert failures and "ffmpeg" in failures[0]
