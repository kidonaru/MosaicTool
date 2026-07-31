"""書き出し時のフレーム→パス索引の検証"""
import random

from mosaic_tool.video.exporter import FramePathIndex


def naive(frame_paths, frame):
    """索引を使わない素直な線形走査(索引の期待値として使う)"""
    return [p for start, end, p in frame_paths if start <= frame <= end]


class TestFramePathIndex:
    def test_empty(self):
        assert FramePathIndex([]).paths_at(0) == []

    def test_single_interval_bounds_are_inclusive(self):
        index = FramePathIndex([(10, 20, "a")])
        assert index.paths_at(9) == []
        assert index.paths_at(10) == ["a"]
        assert index.paths_at(20) == ["a"]
        assert index.paths_at(21) == []

    def test_single_frame_interval(self):
        # 自動検出は開始 == 終了の区間を大量に作るため、その形を明示的に確認する
        index = FramePathIndex([(5, 5, "a")])
        assert index.paths_at(5) == ["a"]
        assert index.paths_at(4) == []
        assert index.paths_at(6) == []

    def test_overlapping_intervals_all_hit(self):
        index = FramePathIndex([(0, 30, "a"), (10, 15, "b"), (12, 40, "c")])
        assert index.paths_at(12) == ["a", "b", "c"]
        assert index.paths_at(20) == ["a", "c"]
        assert index.paths_at(35) == ["c"]

    def test_keeps_original_order(self):
        # 索引は開始フレーム順に並べ替えるが、返す順は元のリスト順を保つ
        index = FramePathIndex([(20, 30, "a"), (0, 40, "b"), (10, 25, "c")])
        assert index.paths_at(22) == ["a", "b", "c"]

    def test_long_interval_before_short_ones(self):
        # 先頭の長い区間を打ち切り判定で飛ばしてしまわないことの確認
        entries = [(0, 100, "long")] + [(i, i, f"s{i}") for i in range(1, 50)]
        index = FramePathIndex(entries)
        assert index.paths_at(30) == ["long", "s30"]
        assert index.paths_at(80) == ["long"]

    def test_matches_naive_on_random_data(self):
        rng = random.Random(1234)
        entries = []
        for i in range(300):
            start = rng.randrange(0, 500)
            end = start + rng.randrange(0, 60)
            entries.append((start, end, f"p{i}"))
        index = FramePathIndex(entries)
        for frame in range(-5, 570):
            assert index.paths_at(frame) == naive(entries, frame)

    def test_matches_naive_on_random_access_order(self):
        # 書き出しはフレーム順に進むが、掃引をやり直す経路も正しさを保つ
        rng = random.Random(99)
        entries = [(10, 40, "a"), (0, 100, "b"), (25, 25, "c"), (60, 70, "d")]
        index = FramePathIndex(entries)
        frames = list(range(-2, 110))
        rng.shuffle(frames)
        for frame in frames:
            assert index.paths_at(frame) == naive(entries, frame)

    def test_long_interval_does_not_keep_short_ones_active(self):
        # 動画全体を覆う区間が 1 本あっても、保持する区間数がそのフレームに
        # 掛かっている数だけに収まること(線形走査へ退化しないことの担保)
        entries = [(0, 10000, "long")] + [(i, i, f"s{i}") for i in range(10000)]
        index = FramePathIndex(entries)
        for frame in range(0, 10000, 500):
            assert index.paths_at(frame) == ["long", f"s{frame}"]
            assert len(index._active) == 2
