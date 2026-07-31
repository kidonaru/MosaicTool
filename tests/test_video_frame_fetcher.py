"""シーク用フレームフェッチャー(非同期取り出しと要求の合流)の検証"""
import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video import frame_fetcher  # noqa: E402
from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
from mosaic_tool.video.frame_fetcher import FrameFetcher  # noqa: E402

INFO = VideoInfo(64, 48, 30.0, "30/1", 300, 10.0, None)

# スレッドの完了を待つ上限 (秒)
WAIT_S = 5.0


def wait_for(condition):
    deadline = time.monotonic() + WAIT_S
    while not condition():
        assert time.monotonic() < deadline, "時間内に条件を満たしませんでした"
        time.sleep(0.005)


def make_fetcher(monkeypatch, extract):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(FrameFetcher, "_extract", extract)
    fetcher = FrameFetcher(Path("movie.mp4"), INFO)
    results = []
    # ワーカースレッド上で直接受け取り、イベントループなしで検証する
    fetcher.frame_ready.connect(
        lambda f, d: results.append((f, d)), Qt.ConnectionType.DirectConnection
    )
    fetcher.start()
    return fetcher, results


class TestFrameFetcher:
    def test_emits_the_extracted_frame(self, monkeypatch):
        fetcher, results = make_fetcher(
            monkeypatch, lambda self, frame: b"data%d" % frame
        )
        try:
            fetcher.request(7)
            wait_for(lambda: results)
            assert results == [(7, b"data7")]
        finally:
            fetcher.stop()

    def test_requests_during_extraction_coalesce_to_the_latest(self, monkeypatch):
        release = threading.Event()
        calls = []

        def extract(self, frame):
            calls.append(frame)
            if frame == 1:
                release.wait(WAIT_S)
            return b"data%d" % frame

        fetcher, results = make_fetcher(monkeypatch, extract)
        try:
            fetcher.request(1)
            wait_for(lambda: calls)  # 1 の取り出し中に後続の要求を積む
            fetcher.request(2)
            fetcher.request(3)
            release.set()
            wait_for(lambda: results)
            # 2 は 3 に置き換えられて取り出されず、古い 1 は表示されない
            assert calls == [1, 3]
            assert results == [(3, b"data3")]
        finally:
            fetcher.stop()

    def test_extraction_failure_reports_without_stopping(self, monkeypatch):
        def extract(self, frame):
            if frame == 1:
                raise frame_fetcher.video_ffmpeg.VideoError("失敗")
            return b"data%d" % frame

        fetcher, results = make_fetcher(monkeypatch, extract)
        failures = []
        fetcher.failed.connect(
            lambda f, m: failures.append(f), Qt.ConnectionType.DirectConnection
        )
        try:
            fetcher.request(1)
            wait_for(lambda: failures)
            fetcher.request(2)
            wait_for(lambda: results)
            assert failures == [1]
            assert results == [(2, b"data2")]
        finally:
            fetcher.stop()

    def test_stop_terminates_the_thread(self, monkeypatch):
        fetcher, _ = make_fetcher(monkeypatch, lambda self, frame: b"data")
        fetcher.stop()
        assert not fetcher.isRunning()


class BlockingProc:
    """kill されるまで communicate が返らない ffmpeg プロセスのふり"""

    def __init__(self):
        self._killed = threading.Event()
        self.returncode = None

    def communicate(self, timeout=None):
        self._killed.wait(WAIT_S)
        self.returncode = 1
        return b"", b"killed"

    def poll(self):
        return self.returncode

    def kill(self):
        self._killed.set()


class TestStopDuringExtraction:
    def test_stop_kills_the_extraction_in_flight(self, monkeypatch):
        """取り出し中に stop() したら、完了を待たずプロセスを切って戻ること"""
        QApplication.instance() or QApplication([])
        procs = []

        def fake_popen(cmd, **kwargs):
            proc = BlockingProc()
            procs.append(proc)
            return proc

        monkeypatch.setattr(frame_fetcher.subprocess, "Popen", fake_popen)
        fetcher = FrameFetcher(Path("movie.mp4"), INFO)
        fetcher.start()
        fetcher.request(1)
        wait_for(lambda: procs)
        start = time.monotonic()
        fetcher.stop()
        assert time.monotonic() - start < WAIT_S / 2  # kill で即座に解ける
        assert not fetcher.isRunning()
