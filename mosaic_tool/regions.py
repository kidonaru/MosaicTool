"""範囲モデル: 矩形/ペンストロークの形状と変形(GUI 非依存)"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QPolygonF, QTransform


# 同じ範囲とみなす重なり率(外接矩形の IoU)
DUPLICATE_IOU = 0.9


class RegionKind(Enum):
    RECT = "rect"
    STROKE = "stroke"
    POLYGON = "polygon"  # points を閉じた多角形として扱う(検出マスクの輪郭)


@dataclass
class Region:
    """モザイク範囲 1 個。形状はローカル座標、変形は中心原点の scale→rotate→pos 平行移動"""

    kind: RegionKind
    rect: QRectF | None = None                       # RECT: ローカル座標の矩形
    points: list[QPointF] = field(default_factory=list)  # STROKE: ローカル座標の点列
    pen_width: float = 20.0
    pos: QPointF = field(default_factory=QPointF)    # 画像座標での平行移動
    rotation: float = 0.0                            # 度
    scale_x: float = 1.0                             # 中心原点の横方向倍率
    scale_y: float = 1.0                             # 中心原点の縦方向倍率

    def local_path(self) -> QPainterPath:
        """変形前のローカル形状パスを返す"""
        path = QPainterPath()
        if self.kind is RegionKind.RECT:
            path.addRect(self.rect)
            return path
        if self.kind is RegionKind.POLYGON:
            # 検出マスクの輪郭。始点と終点をつないだ閉じた図形にする
            path.addPolygon(QPolygonF(self.points))
            path.closeSubpath()
            return path
        # ストローク: 点列をつないだ折れ線を太らせた輪郭
        path.moveTo(self.points[0])
        if len(self.points) == 1:
            # 長さ 0 の線分はストローク化で空になるため微小線分にする(1 クリックで点を打てるように)
            path.lineTo(self.points[0] + QPointF(0.01, 0))
        else:
            for pt in self.points[1:]:
                path.lineTo(pt)
        stroker = QPainterPathStroker()
        stroker.setWidth(self.pen_width)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        # simplified() で自己交差を解消し、合成後の外周だけのアウトラインにする
        return stroker.createStroke(path).simplified()

    def image_transform(self) -> QTransform:
        """ローカル座標 → 画像座標の変換(中心原点で scale→rotate、pos で移動)"""
        o = self.local_path().boundingRect().center()
        t = QTransform()
        t.translate(self.pos.x() + o.x(), self.pos.y() + o.y())
        t.rotate(self.rotation)
        t.scale(self.scale_x, self.scale_y)
        t.translate(-o.x(), -o.y())
        return t

    def image_path(self) -> QPainterPath:
        """画像座標での範囲パスを返す(マスク生成に使う)"""
        return self.image_transform().map(self.local_path())


def _iou(a: QRectF, b: QRectF) -> float:
    """外接矩形どうしの重なり率 (0.0〜1.0)"""
    inter = a.intersected(b)
    if inter.isEmpty():
        return 0.0
    intersection = inter.width() * inter.height()
    union = a.width() * a.height() + b.width() * b.height() - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def drop_duplicate_regions(
    regions: list[Region], existing: list[Region], iou: float = DUPLICATE_IOU
) -> list[Region]:
    """既存とほぼ同じ位置・大きさの範囲を取り除いて返す

    同じ画像に検出を繰り返したときや、複数のモデルが同じ対象を捉えたときに
    同じ範囲が積み上がるのを防ぐ。判定は外接矩形の重なり率で行い、
    残す側どうしの重複も 1 つに絞る。
    """
    bounds = [region.image_path().boundingRect() for region in existing]
    kept: list[Region] = []
    for region in regions:
        rect = region.image_path().boundingRect()
        if any(_iou(rect, other) >= iou for other in bounds):
            continue
        bounds.append(rect)
        kept.append(region)
    return kept
