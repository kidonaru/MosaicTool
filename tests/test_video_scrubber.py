"""スクラバー(長寿命パイプによるプロキシフレーム取り出し)の検証"""
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
from mosaic_tool.video.scrubber import Scrubber  # noqa: E402

# proxy_size(INFO) == (64, 48) になる小さな動画。fps=30 なので読み飛ばし上限は 30
INFO = VideoInfo(64, 48, 30.0, "30/1", 300, 10.0, None)
FRAME_BYTES = 64 * 48 * 3

WAIT_S = 5.0


class FakeProc:
    """start 以降のフレームを順に流す ffmpeg プロセスのふり

    フレーム k の中身は k % 256 で埋め、どのフレームが返ったか判別できるようにする。
    """

    def __init__(self, start: int, total: int):
        self._chunks = b"".join(
            bytes([k % 256]) * FRAME_BYTES for k in range(start, total)
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


def make_scrubber(total=300):
    QApplication.instance() or QApplication([])

    class FakeScrubber(Scrubber):
        opens: list[int]

        def _open(self, frame):
            self.opens.append(frame)
            return FakeProc(frame, total)

    scrubber = FakeScrubber(Path("movie.mp4"), INFO)
    scrubber.opens = []
    return scrubber


class TestServe:
    def test_first_request_opens_a_pipe_at_the_frame(self):
        scrubber = make_scrubber()
        data = scrubber._serve(5)
        assert data[0] == 5
        assert scrubber.opens == [5]

    def test_forward_seek_within_the_limit_reuses_the_pipe(self):
        scrubber = make_scrubber()
        scrubber._serve(5)
        data = scrubber._serve(9)
        assert data[0] == 9
        assert scrubber.opens == [5]  # 読み飛ばしのみで再起動しない

    def test_backward_seek_restarts_the_pipe(self):
        scrubber = make_scrubber()
        scrubber._serve(5)
        data = scrubber._serve(3)
        assert data[0] == 3
        assert scrubber.opens == [5, 3]

    def test_far_forward_seek_restarts_the_pipe(self):
        scrubber = make_scrubber()
        scrubber._serve(5)  # 次は 6。上限は 6 + 30
        data = scrubber._serve(37)
        assert data[0] == 37
        assert scrubber.opens == [5, 37]

    def test_repeated_single_steps_reuse_the_pipe(self):
        scrubber = make_scrubber()
        for frame in range(10, 20):
            data = scrubber._serve(frame)
            assert data[0] == frame
        assert scrubber.opens == [10]

    def test_eof_reports_failure(self):
        scrubber = make_scrubber(total=10)
        failures = []
        scrubber.failed.connect(
            lambda f, m: failures.append(f), Qt.ConnectionType.DirectConnection
        )
        assert scrubber._serve(12) is None
        assert failures == [12]


class TestThread:
    def test_requested_frame_is_emitted_as_a_proxy_image(self):
        scrubber = make_scrubber()
        results = []
        scrubber.frame_ready.connect(
            lambda f, img: results.append((f, img)),
            Qt.ConnectionType.DirectConnection,
        )
        scrubber.start()
        try:
            scrubber.request(7)
            deadline = time.monotonic() + WAIT_S
            while not results:
                assert time.monotonic() < deadline, "時間内に届きませんでした"
                time.sleep(0.005)
            frame, image = results[0]
            assert frame == 7
            assert (image.width(), image.height()) == (64, 48)
        finally:
            scrubber.stop()
        assert not scrubber.isRunning()
