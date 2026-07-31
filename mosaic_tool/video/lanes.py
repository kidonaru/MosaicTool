"""タイムラインの行構成: カテゴリ分類と重ならない区間のレーン詰め"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

from mosaic_tool.video.session import RegionSource, VideoRegion

# タイムラインの行の並び順と表示ラベル
CATEGORY_ORDER = [RegionSource.PEN, RegionSource.RECT, RegionSource.AUTO]
CATEGORY_LABELS = {
    RegionSource.PEN: "ペン",
    RegionSource.RECT: "矩形",
    RegionSource.AUTO: "自動検出",
}


@dataclass
class TimelineLane:
    """タイムラインの 1 行(同一カテゴリ内で重ならない区間の集まり)"""

    source: RegionSource
    items: list[VideoRegion]


def pack_lanes(intervals: list[tuple[int, int]]) -> list[list[int]]:
    """重ならない区間を同じレーンへ詰め、レーンごとの入力 index を返す

    開始フレーム順に見て「最も早く終わるレーン」へ min-heap で割り当てる
    区間パーティショニング O(n log n)。自動検出でフレームごとの独立区間が
    数千個並んでも実用速度を保つ。各レーンの index は開始フレーム順に並ぶ。
    区間は両端含みのため、end == 次の start は重なりとして扱う。
    """
    lanes: list[list[int]] = []
    heap: list[tuple[int, int]] = []  # (レーン最後の終了フレーム, レーン番号)
    for i in sorted(range(len(intervals)), key=lambda i: intervals[i]):
        start, end = intervals[i]
        if heap and heap[0][0] < start:
            # 最も早く終わったレーンが空いていれば再利用する
            _, lane = heapq.heappop(heap)
        else:
            lane = len(lanes)
            lanes.append([])
        lanes[lane].append(i)
        heapq.heappush(heap, (end, lane))
    return lanes


def build_rows(regions: list[VideoRegion]) -> list[TimelineLane]:
    """カテゴリ順にレーン詰めした行リストを作る(空カテゴリは行を作らない)"""
    rows: list[TimelineLane] = []
    for source in CATEGORY_ORDER:
        group = [vr for vr in regions if vr.source is source]
        rows += [
            TimelineLane(source, [group[i] for i in lane])
            for lane in pack_lanes([(vr.start, vr.end) for vr in group])
        ]
    return rows
