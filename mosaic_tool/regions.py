"""範囲モデル: 矩形/ペンストロークの形状と変形(GUI 非依存)"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QTransform


class RegionKind(Enum):
    RECT = "rect"
    STROKE = "stroke"


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
