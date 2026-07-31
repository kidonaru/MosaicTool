"""タイムラインの行構成: カテゴリ分類と重ならない区間のレーン詰め"""
from __future__ import annotations

from bisect import bisect_left, insort
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF

from mosaic_tool.regions import bbox_iou
from mosaic_tool.video.session import RegionSource, VideoRegion

# 直前フレームの区間と同じ行へ続けるとみなす最小の重なり率。外れても最上段詰めへ
# 落ちるだけなので、対象が速く動く場合も拾えるよう緩めに取る
LANE_MATCH_IOU = 0.1

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


def _group_by_start(
    intervals: list[tuple[int, int]], order: list[int]
) -> list[tuple[int, list[int]]]:
    """開始フレームが同じ index をまとめる(order は開始フレーム順)

    同一フレームの検出をまとめて 1 対 1 で継続先へ割り当てるために使う。
    """
    groups: list[tuple[int, list[int]]] = []
    for i in order:
        start = intervals[i][0]
        if groups and groups[-1][0] == start:
            groups[-1][1].append(i)
        else:
            groups.append((start, [i]))
    return groups


def _match_tails(
    intervals: list[tuple[int, int]],
    rects: list[QRectF],
    occupied: list[list[tuple[int, int]]],
    tails: dict[int, list[tuple[int, QRectF]]],
    group: list[int],
    start: int,
    occupy: Callable[[int, int], None],
) -> None:
    """直前フレームで終わるレーンへ、重なりの大きい区間から順に続ける

    候補は「そのフレームで生存しているトラック」だけなので、総当たりでも
    区間数に対しては線形に収まる。同点は index とレーン番号で決めて、
    入力順が変わっても結果が揺れないようにする。
    """
    candidates = tails.get(start - 1, [])
    if not candidates:
        return
    pairs = [
        (-iou, i, lane)
        for i in group
        for lane, rect in candidates
        if _fits(occupied[lane], *intervals[i])
        and (iou := bbox_iou(rects[i], rect)) >= LANE_MATCH_IOU
    ]
    used_items: set[int] = set()
    used_lanes: set[int] = set()
    for _, i, lane in sorted(pairs):
        if i in used_items or lane in used_lanes:
            continue
        used_items.add(i)
        used_lanes.add(lane)
        occupy(i, lane)


def place_lanes(
    intervals: list[tuple[int, int]],
    lanes: list[int | None],
    rects: list[QRectF] | None = None,
) -> list[int]:
    """区間をレーンへ割り当て、入力 index ごとのレーン番号を返す

    lanes[i] が None でなければそのレーンを先に確保する。手動同士が同じ
    レーンで被った場合は、開始フレーム順で後ろに来た方を自動配置へ落とす
    (通常はドラッグ確定時に解消済みで、ここは防御)。

    rects を渡すと、直前フレームで終わっているレーンのうち外接矩形の重なりが
    最大のものへ続けて置く。自動検出は追跡をしないため、同一フレームに複数の
    対象が居ると検出順で行が入れ替わってしまう。それを形状で結び直す。
    残り(新しく現れた対象)は開始フレーム順に最上段の空きレーンへ詰める。
    「最も早く終わったレーン」を選ぶと重ならないチェーンが千鳥状に散らばる
    ため、必ず最上段へ詰める。空きの判定は二分探索なので、自動検出で区間が
    数千個並んでも実用速度を保つ。
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i])
    occupied: list[list[tuple[int, int]]] = []  # レーンごとの占有区間(開始順)
    tails: dict[int, list[tuple[int, QRectF]]] = {}  # 終端フレーム -> (レーン, 矩形)
    assigned: list[int] = [-1] * len(intervals)

    def occupy(i: int, lane: int) -> None:
        """区間 i をレーンへ確定し、継続マッチ用の索引も更新する"""
        insort(occupied[lane], intervals[i])
        assigned[i] = lane
        if rects is not None:
            tails.setdefault(intervals[i][1], []).append((lane, rects[i]))

    for i in order:
        lane = lanes[i]
        if lane is None:
            continue
        while len(occupied) <= lane:
            occupied.append([])
        if _fits(occupied[lane], *intervals[i]):
            occupy(i, lane)

    rest = [i for i in order if assigned[i] < 0]
    for start, group in _group_by_start(intervals, rest):
        if rects is not None:
            _match_tails(intervals, rects, occupied, tails, group, start, occupy)
        for i in group:
            if assigned[i] >= 0:
                continue
            lane = 0
            while lane < len(occupied) and not _fits(occupied[lane], *intervals[i]):
                lane += 1
            if lane == len(occupied):
                occupied.append([])
            occupy(i, lane)
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
    空く場合も、行番号と表示行がずれないよう空の行を残す。自動検出だけは
    外接矩形も渡し、直前フレームと重なる区間を同じ行へ続ける。
    """
    rows: list[TimelineLane] = []
    for source in CATEGORY_ORDER:
        group = [vr for vr in regions if vr.source is source]
        if not group:
            continue
        rects = None
        if source is RegionSource.AUTO:
            rects = [vr.region.image_path().boundingRect() for vr in group]
        assigned = place_lanes(
            [(vr.start, vr.end) for vr in group], [vr.lane for vr in group], rects
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
