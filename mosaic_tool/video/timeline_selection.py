"""タイムラインの選択集合と一括編集(Qt に依存しない純ロジック)

選択の持ち主はタイムライン(video/timeline_window.py)で、ここは集合の出入りと
一括編集の移動量の計算だけを担う。ウィジェット無しで検証できるように分けている。
"""
from __future__ import annotations

from mosaic_tool.regions import Region
from mosaic_tool.video.session import VideoRegion

# 一括編集の種類。move は平行移動、start / end は片側の端の伸び縮み
MOVE = "move"
START = "start"
END = "end"


def _unique(items: list[VideoRegion]) -> list[VideoRegion]:
    """同一インスタンスの重複を最初の出現だけ残して除く"""
    seen: set[int] = set()
    out: list[VideoRegion] = []
    for vr in items:
        if id(vr) not in seen:
            seen.add(id(vr))
            out.append(vr)
    return out


class TimelineSelection:
    """選択中の区間の集合

    VideoRegion と Region は dataclass のため == はフィールド比較になり、
    値の同じ別区間を同一視してしまう。よって同一インスタンス比較で持つ。
    選んだ順を保ち、削除や一括編集の対象順が見た目と食い違わないようにする。
    """

    def __init__(self) -> None:
        self._items: list[VideoRegion] = []

    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[VideoRegion]:
        return list(self._items)

    def regions(self) -> list[Region]:
        """選択中の区間が指すモザイク範囲(キャンバスへの反映用)"""
        return [vr.region for vr in self._items]

    def contains(self, vr: VideoRegion) -> bool:
        return any(v is vr for v in self._items)

    def replace(self, items: list[VideoRegion]) -> None:
        self._items = _unique(items)

    def add(self, items: list[VideoRegion]) -> None:
        self._items = _unique(self._items + list(items))

    def toggle(self, vr: VideoRegion) -> None:
        if self.contains(vr):
            self._items = [v for v in self._items if v is not vr]
        else:
            self._items.append(vr)

    def clear(self) -> None:
        self._items = []

    def prune(self, regions: list[VideoRegion]) -> None:
        """セッションに残っている区間だけを選択に残す"""
        alive = {id(vr) for vr in regions}
        self._items = [v for v in self._items if id(v) in alive]


def _delta_limits(
    items: list[VideoRegion], kind: str, max_frame: int
) -> tuple[int, int]:
    """選択全体が収まる移動量の下限と上限"""
    if kind == MOVE:
        return max(-vr.start for vr in items), min(max_frame - vr.end for vr in items)
    if kind == START:
        # 0 を下回らず、自分の終了フレームを越えない
        return (
            max(-vr.start for vr in items),
            min(vr.end - vr.start for vr in items),
        )
    # 自分の開始フレームを下回らず、末尾フレームを越えない
    return (
        max(vr.start - vr.end for vr in items),
        min(max_frame - vr.end for vr in items),
    )


def clamp_delta(
    items: list[VideoRegion], kind: str, delta: int, max_frame: int
) -> int:
    """選択全体を kind の向きへずらせる量へ delta を丸める

    1 つでも許容範囲を外れたら全体をその分で止める。個別にクランプすると
    選択内の相対位置が崩れ、区間の並びが意図せず詰まる。
    """
    if not items:
        return 0
    low, high = _delta_limits(items, kind, max_frame)
    if low > high:
        # 既に末尾を越えている区間が混ざっている場合。動かさない
        return 0
    return max(low, min(delta, high))


def apply_delta(items: list[VideoRegion], kind: str, delta: int) -> None:
    """clamp_delta で丸めた移動量を選択全体へ当てる"""
    for vr in items:
        if kind == MOVE:
            vr.start += delta
            vr.end += delta
        elif kind == START:
            vr.start += delta
        else:
            vr.end += delta
