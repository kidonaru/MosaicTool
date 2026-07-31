"""再生エンジン(目標フレームの算出・rawvideo の切り出し・終端の扱い)の検証"""
import os
import queue

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
from mosaic_tool.video.player import (  # noqa: E402
    VideoPlayer,
    split_frames,
    target_frame,
)


def make_info(frame_count=100):
    return VideoInfo(64, 48, 30.0, "30/1", frame_count, frame_count / 30.0, None)


class TestTargetFrame:
    def test_start_of_playback_stays_on_the_start_frame(self):
        assert target_frame(10, 0, 30.0, 1.0, 99) == 10

    def test_advances_with_the_frame_rate(self):
        assert target_frame(0, 1000, 30.0, 1.0, 99) == 30

    def test_speed_scales_the_advance(self):
        assert target_frame(0, 1000, 30.0, 2.0, 99) == 60
        assert target_frame(0, 1000, 30.0, 0.5, 99) == 15

    def test_clamped_to_the_last_frame(self):
        assert target_frame(0, 10_000, 30.0, 1.0, 99) == 99


class TestSplitFrames:
    def test_takes_complete_frames_and_keeps_the_rest(self):
        buffer = bytearray(b"aaabbbc")
        frames = split_frames(buffer, 3)
        assert frames == [b"aaa", b"bbb"]
        assert bytes(buffer) == b"c"

    def test_no_complete_frame(self):
        buffer = bytearray(b"ab")
        assert split_frames(buffer, 3) == []
        assert bytes(buffer) == b"ab"


class TestVideoPlayer:
    def test_starting_at_the_last_frame_finishes_immediately(self, tmp_path):
        QApplication.instance() or QApplication([])
        player = VideoPlayer(tmp_path / "movie.mp4", make_info(100))
        fired = []
        player.finished.connect(lambda: fired.append(True))
        player.start(99)
        assert fired == [True]
        assert not player.is_playing()

    def test_speed_is_applied_without_playing(self, tmp_path):
        QApplication.instance() or QApplication([])
        player = VideoPlayer(tmp_path / "movie.mp4", make_info(100))
        player.set_speed(2.0)
        assert player._speed == 2.0


class FakeClock:
    """経過時間を手で進められる時計(QElapsedTimer の代役)"""

    def __init__(self):
        self.now = 0
        self.base = 0

    def start(self):
        self.base = self.now

    def restart(self):
        self.base = self.now

    def elapsed(self):
        return self.now - self.base


class FakeReader:
    """稼働中の読み出しスレッドのふり(キューへ手でフレームを積む)"""

    def __init__(self):
        self.queue = queue.Queue()

    def fill(self, count=30):
        while self.queue.qsize() < count:
            self.queue.put(b"\x00" * (64 * 48 * 3))

    def isRunning(self):  # noqa: N802 (QThread のインターフェイスに合わせる)
        return True


def make_ticking_player(tmp_path):
    """再生中の状態を組み立てた (player, clock, reader) を返す"""
    QApplication.instance() or QApplication([])
    player = VideoPlayer(tmp_path / "movie.mp4", make_info(300))
    clock = FakeClock()
    reader = FakeReader()
    player._clock = clock
    player._reader = reader
    player._start = player._index = player._next = 0
    clock.start()
    return player, clock, reader


class TestOnTick:
    def test_playback_advances_in_real_time_with_a_full_queue(self, tmp_path):
        """キューが満たされていれば tick を重ねるだけで実時間どおり進む

        「次のフレームの表示時刻がまだ来ていない」だけの tick を枯渇と
        誤判定して時計を巻き戻すと、再生がほぼ進まなくなる(回帰確認)。
        """
        player, clock, reader = make_ticking_player(tmp_path)
        shown = []
        player.frame_ready.connect(lambda i, img: shown.append(i))
        # 実運用と同じ 10ms 刻みで 1 秒ぶん tick する(30fps なら約 30 フレーム)
        for _ in range(100):
            clock.now += 10
            reader.fill()
            player._on_tick()
        assert len(shown) >= 25

    def test_a_tick_before_the_next_frame_is_due_keeps_the_clock(self, tmp_path):
        player, clock, reader = make_ticking_player(tmp_path)
        reader.fill()
        clock.now += 40  # フレーム 1 を表示して _next=2 まで進める
        player._on_tick()
        clock.now += 10  # 50ms 時点。フレーム 2 (66ms) はまだ先
        player._on_tick()
        assert clock.elapsed() == 50  # 時計は巻き戻らない

    def test_starvation_resets_the_clock_to_the_shown_frame(self, tmp_path):
        player, clock, reader = make_ticking_player(tmp_path)
        reader.fill(1)
        clock.now += 40
        player._on_tick()  # フレーム 1 まで表示しキューが尽きる
        clock.now += 100  # 次のフレームが必要になるまで進める
        player._on_tick()
        # デコード待ちの間は時計を張り直し、復帰時にまとめてコマ落ちしない
        assert clock.elapsed() == 0
        assert player._start == player._index
