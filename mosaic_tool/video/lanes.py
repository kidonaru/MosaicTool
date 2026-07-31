"""タイムラインの行構成: カテゴリ分類と重ならない区間のレーン詰め"""
from __future__ import annotations

from bisect import bisect_left, insort
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


def _fits(items: list[tuple[int, int]], start: int, end: int) -> bool:
    """レーンの占有区間(開始順)へ [start, end] を足せるか

    両端含みの区間なので、隣と端が接しただけでも重なりとして弾く。
    """
    i = bisect_left(items, (start, end))
    if i > 0 and items[i - 1][1] >= start:
        return False
    return not (i < len(items) and items[i][0] <= end)


def place_lanes(
    intervals: list[tuple[int, int]], lanes: list[int | None]
) -> list[int]:
    """区間をレーンへ割り当て、入力 index ごとのレーン番号を返す

    lanes[i] が None でなければそのレーンを先に確保する。手動同士が同じ
    レーンで被った場合は、開始フレーム順で後ろに来た方を自動配置へ落とす
    (通常はドラッグ確定時に解消済みで、ここは防御)。残りは開始フレーム順に
    最上段の空きレーンへ詰める。「最も早く終わったレーン」を選ぶと重ならない
    チェーンが千鳥状に散らばるため、必ず最上段へ詰める。空きの判定は二分探索
    なので、自動検出で区間が数千個並んでも実用速度を保つ。
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i])
    occupied: list[list[tuple[int, int]]] = []  # レーンごとの占有区間(開始順)
    assigned: list[int] = [-1] * len(intervals)
    for i in order:
        lane = lanes[i]
        if lane is None:
            continue
        while len(occupied) <= lane:
            occupied.append([])
        if _fits(occupied[lane], *intervals[i]):
            insort(occupied[lane], intervals[i])
            assigned[i] = lane
    for i in order:
        if assigned[i] >= 0:
            continue
        start, end = intervals[i]
        lane = 0
        while lane < len(occupied) and not _fits(occupied[lane], start, end):
            lane += 1
        if lane == len(occupied):
            occupied.append([])
        insort(occupied[lane], (start, end))
        assigned[i] = lane
    return assigned


def pack_lanes(intervals: list[tuple[int, int]]) -> list[list[int]]:
    """重ならない区間を同じレーンへ詰め、レーンごとの入力 index を返す

    手動指定なしの place_lanes と等価。各レーンの index は開始フレーム順に並ぶ。
    """
    assigned = place_lanes(intervals, [None] * len(intervals))
    lanes: list[list[int]] = [[] for _ in range(max(assigned, default=-1) + 1)]
    for i in sorted(range(len(intervals)), key=lambda i: intervals[i]):
        lanes[assigned[i]].append(i)
    return lanes


def build_rows(regions: list[VideoRegion]) -> list[TimelineLane]:
    """カテゴリ順にレーン詰めした行リストを作る(空カテゴリは行を作らない)

    手動で行を指定した区間はその行を優先して確保する。指定によって上の行が
    空く場合も、行番号と表示行がずれないよう空の行を残す。
    """
    rows: list[TimelineLane] = []
    for source in CATEGORY_ORDER:
        group = [vr for vr in regions if vr.source is source]
        if not group:
            continue
        assigned = place_lanes(
            [(vr.start, vr.end) for vr in group], [vr.lane for vr in group]
        )
        buckets: list[list[VideoRegion]] = [[] for _ in range(max(assigned) + 1)]
        for vr, lane in zip(group, assigned):
            buckets[lane].append(vr)
        rows += [
            TimelineLane(source, sorted(items, key=lambda v: (v.start, v.end)))
            for items in buckets
        ]
    return rows


def clamp_lane_delta(current: list[int], limits: list[int], delta: int) -> int:
    """選択全体を上下へずらせる行数へ delta を丸める

    limits[i] はカテゴリのレーン数。末尾に 1 行足せるようにするため、
    レーン番号の上限は limits[i] そのものになる。個別にクランプすると
    選択内の上下関係が崩れるので、1 つでも外れたら全体をその分で止める。
    """
    if not current:
        return 0
    low = max(-lane for lane in current)
    high = min(limit - lane for lane, limit in zip(current, limits))
    if low > high:
        return 0
    return max(low, min(delta, high))
