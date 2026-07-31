"""動画編集の状態: 区間つき範囲の管理とキャンバスとの同期"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF

from mosaic_tool.detect.convert import (
    MIN_POLYGON_POINTS,
    POLYGON_SIMPLIFY_RATIO,
    thin_points,
)
from mosaic_tool.regions import Region, RegionKind
from mosaic_tool.video.ffmpeg import VideoInfo
from mosaic_tool.video.merge import Interval


class RegionSource(Enum):
    """範囲の由来。タイムラインの行分類に使う"""

    PEN = "pen"
    RECT = "rect"
    AUTO = "auto"


# RegionKind からカテゴリ由来を導く対応。手描きの多角形は存在しないため
# POLYGON は自動検出とみなす
_SOURCE_BY_KIND = {
    RegionKind.STROKE: RegionSource.PEN,
    RegionKind.RECT: RegionSource.RECT,
    RegionKind.POLYGON: RegionSource.AUTO,
}


@dataclass
class VideoRegion:
    """モザイク範囲 1 個と適用区間(両端のフレームを含む)"""

    region: Region
    start: int
    end: int
    # タイムラインの行分類。省略時は形状から導出する
    source: RegionSource | None = None
    # タイムラインの行(カテゴリ内のレーン番号)。None は自動配置に任せる
    lane: int | None = None

    def __post_init__(self) -> None:
        if self.source is None:
            self.source = _SOURCE_BY_KIND[self.region.kind]

    def covers(self, frame: int) -> bool:
        return self.start <= frame <= self.end


class VideoSession:
    """開いている動画 1 本の編集状態

    キャンバスは「表示中フレームに掛かる範囲」だけを持つため、フレーム切替や
    範囲の増減のたびに sync_from_canvas で区間リストと突き合わせる。
    Region はキャンバスとここで同一インスタンスを共有し、変形は自動で反映される。
    """

    def __init__(self, path: Path, info: VideoInfo):
        self.path = path
        self.info = info
        self.frame = 0
        self.regions: list[VideoRegion] = []

    def regions_at(self, frame: int) -> list[Region]:
        """指定フレームに掛かる範囲(キャンバス表示用)"""
        return [vr.region for vr in self.regions if vr.covers(frame)]

    def find(self, region: Region) -> VideoRegion | None:
        """キャンバス上の Region から区間エントリを引く(同一インスタンス比較)"""
        return next((vr for vr in self.regions if vr.region is region), None)

    def sync_from_canvas(self, canvas_regions: list[Region]) -> None:
        """表示中フレームのキャンバス内容を区間リストへ反映する

        キャンバスに現れた未知の範囲は現在フレームのみの区間として追加し、
        現在フレームに掛かるのにキャンバスから消えた範囲は削除されたとみなす。
        """
        known = {id(vr.region) for vr in self.regions}
        shown = {id(r) for r in canvas_regions}
        for region in canvas_regions:
            if id(region) not in known:
                self.regions.append(VideoRegion(region, self.frame, self.frame))
        self.regions = [
            vr for vr in self.regions
            if id(vr.region) in shown or not vr.covers(self.frame)
        ]

    def set_start(self, region: Region, frame: int) -> bool:
        """区間の開始を frame にする。終了より後なら終了も合わせる"""
        vr = self.find(region)
        if vr is None:
            return False
        vr.start = frame
        vr.end = max(vr.end, frame)
        return True

    def set_end(self, region: Region, frame: int) -> bool:
        """区間の終了を frame にする。開始より前なら開始も合わせる"""
        vr = self.find(region)
        if vr is None:
            return False
        vr.end = frame
        vr.start = min(vr.start, frame)
        return True

    def add_intervals(self, intervals: list[Interval]) -> int:
        """検出区間を範囲として追加し、追加数を返す

        セグメンテーションの輪郭がある区間は多角形、無ければ bbox の矩形。
        """
        for iv in intervals:
            self.regions.append(
                VideoRegion(
                    _interval_region(iv, self.info),
                    iv.start,
                    iv.end,
                    source=RegionSource.AUTO,
                )
            )
        return len(intervals)


def _interval_region(iv: Interval, info: VideoInfo) -> Region:
    """検出区間 1 個を範囲へ変換する"""
    if iv.polygon:
        min_distance = math.hypot(info.width, info.height) * POLYGON_SIMPLIFY_RATIO
        points = [QPointF(x, y) for x, y in iv.polygon]
        thinned = thin_points(points, min_distance)
        if len(thinned) >= MIN_POLYGON_POINTS:
            return Region(kind=RegionKind.POLYGON, points=thinned)
    x1, y1, x2, y2 = iv.bbox
    return Region(kind=RegionKind.RECT, rect=QRectF(x1, y1, x2 - x1, y2 - y1))
