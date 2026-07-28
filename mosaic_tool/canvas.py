"""キャンバス: 画像表示、ズーム/パン、範囲の作成と編集"""
from __future__ import annotations

import math
from enum import Enum, auto
from typing import NamedTuple

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from mosaic_tool.mosaic import (
    cell_grid_to_rects,
    make_mosaic_image,
    mask_to_cell_grid,
    paths_to_mask,
)
from mosaic_tool.regions import Region, RegionKind


class ToolMode(Enum):
    RECT = auto()
    PEN = auto()


HANDLE_SIZE = 9.0     # 変形ハンドルの一辺(画面 px)
ROTATE_OFFSET = 32.0  # 回転ハンドルの枠上辺からの距離(画面 px)
# 枠が小さく見えるときは回転ハンドルが飛び出しすぎるため、枠の表示高さに対する比で頭打ちにする
ROTATE_OFFSET_RATIO = 0.5
ROTATE_OFFSET_MIN = 12.0  # 回転ハンドル距離の下限(画面 px)
MIN_SCALE = 0.05      # 倍率の下限(潰れて操作不能になるのを防ぐ)
ZOOM_STEP = 1.25      # ホイール 1 ノッチあたりの拡大率
VIEW_SCALE_MIN = 0.05  # ビュー表示倍率の下限(画像を見失わないようにする)
VIEW_SCALE_MAX = 20.0  # ビュー表示倍率の上限(拡大しすぎて描画が重くなるのを防ぐ)
HANDLE_HIT_MARGIN = 4.0  # ハンドルの当たり判定を見た目より広げる量(片側/画像座標基準)
# リサイズ時に固定する側のハンドル(ドラッグするハンドルの対角/対辺)
OPPOSITE_HANDLE = {
    "tl": "br", "tr": "bl", "bl": "tr", "br": "tl",
    "t": "b", "b": "t", "l": "r", "r": "l",
}
# 画像未読み込み時にキャンバス中央へ表示する案内
PLACEHOLDER_TITLE = "画像 / フォルダをここにドロップ"
PLACEHOLDER_HINT = "対応形式: PNG / JPEG / WebP / BMP など"
PLACEHOLDER_TITLE_SIZE = 20   # 案内の見出しの文字サイズ(pt)
PLACEHOLDER_HINT_SIZE = 11    # 案内の補足の文字サイズ(pt)
PLACEHOLDER_LINE_GAP = 14     # 見出しと補足の間隔(px)
PLACEHOLDER_FRAME_MARGIN = 40  # 破線枠とビュー端の余白(px)
PLACEHOLDER_FRAME_RADIUS = 12  # 破線枠の角丸半径(px)
# 案内は文字を濃く、枠を薄く描いて主従を付ける(前景色に対する不透明度 0-255)
PLACEHOLDER_TITLE_ALPHA = 200
PLACEHOLDER_HINT_ALPHA = 140
PLACEHOLDER_FRAME_ALPHA = 90


class ResizeGrab(NamedTuple):
    """リサイズのドラッグ開始時に固定する情報"""

    grab_pos: QPointF   # 掴んだ位置(シーン座標)
    pos0: QPointF       # 掴んだ時点のアイテム位置
    span: QPointF       # 固定側 → ハンドル のベクトル(倍率 1.0 分の長さ)
    anchor: QPointF     # 中心 → 固定側 のベクトル
    sx0: float          # 掴んだ時点の倍率
    sy0: float


def with_alpha(color: QColor, alpha: int) -> QColor:
    """色の不透明度だけを差し替えた色を返す"""
    return QColor(color.red(), color.green(), color.blue(), alpha)


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """PIL 画像を QPixmap に変換する"""
    rgba = img.convert("RGBA")
    qimg = QImage(
        rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    return QPixmap.fromImage(qimg.copy())


class MosaicOverlay(QGraphicsItem):
    """全範囲のモザイクプレビューを画像座標で重ね描画するアイテム"""

    def __init__(self, rect: QRectF):
        super().__init__()
        self._rect = rect
        self._mosaic_pixmap: QPixmap | None = None
        self._clip = QPainterPath()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:
        return self._rect

    def set_mosaic(self, pm: QPixmap) -> None:
        self._mosaic_pixmap = pm
        self.update()

    def set_clip(self, path: QPainterPath) -> None:
        self._clip = path
        self.update()

    def paint(self, painter, option, widget=None):
        if self._mosaic_pixmap is None or self._clip.isEmpty():
            return
        painter.setClipPath(self._clip)
        painter.drawPixmap(0, 0, self._mosaic_pixmap)


class RegionItem(QGraphicsObject):
    """モザイク範囲 1 個を表すアイテム(矩形/ストローク共通)"""

    changed = Signal()               # 変形中の逐次通知(プレビュー更新用)
    edited = Signal(object, tuple)   # 変形確定時: (アイテム, 変形前の状態) Undo 用

    def __init__(self, region: Region):
        super().__init__()
        self.region = region
        self._local_path = region.local_path()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._origin = self._local_path.boundingRect().center()
        self.setPos(region.pos)
        self._rot = region.rotation
        self._sx = region.scale_x
        self._sy = region.scale_y
        self._apply_transform()
        self._press_state: tuple | None = None
        self._drag_mode: str | None = None  # None | "resize" | "rotate"
        self._grab_angle = 0.0
        self._resize: ResizeGrab | None = None
        self._view_scale = 1.0   # ビューの表示倍率(ハンドルを画面上一定サイズに保つ)
        self._preview = False    # プレビュー中は枠・ハンドルを描かず操作もしない

    # --- 変形の適用 ---

    def _apply_transform(self, notify: bool = True) -> None:
        """中心原点で 拡縮 → 回転 を行うアイテム変換を組み立てる

        Qt の setRotation/setScale は「回転の後に transform」の順で合成され、
        非一様な拡縮が回転後(シーン軸)に効いて図形が歪むため、
        Region.image_transform と同じ合成を transform 側で完結させる。

        notify=False は直後に setPos が続く場合に使う(setPos 側の通知に任せ、
        重いオーバーレイ再計算が 1 操作で 2 回走るのを避ける)。
        """
        o = self._origin
        t = QTransform()
        t.translate(o.x(), o.y())
        t.rotate(self._rot)
        t.scale(self._sx, self._sy)
        t.translate(-o.x(), -o.y())
        self.prepareGeometryChange()
        self.setTransform(t)
        if notify:
            self.changed.emit()

    @property
    def _safe_scale(self) -> tuple[float, float]:
        """0 除算と潰れを避けるため下限でクランプした倍率"""
        return max(abs(self._sx), MIN_SCALE), max(abs(self._sy), MIN_SCALE)

    @property
    def _handle_scale(self) -> tuple[float, float]:
        """ハンドルの見た目サイズを一定に保つための除数(範囲の倍率 × 表示倍率)"""
        sx, sy = self._safe_scale
        v = max(self._view_scale, MIN_SCALE)
        return sx * v, sy * v

    def set_view_scale(self, scale: float) -> None:
        """ビューのズーム倍率を伝える(ハンドルを画面上一定サイズに保つ)"""
        if scale == self._view_scale:
            return
        self.prepareGeometryChange()
        self._view_scale = scale
        self.update()

    def set_preview(self, on: bool) -> None:
        """プレビュー表示の切替(枠・ハンドルを隠し、選択や変形も無効にする)"""
        self._preview = on
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not on)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, not on)
        if on:
            self.setSelected(False)
        self.update()

    @staticmethod
    def _rotate_vec(v: QPointF, deg: float) -> QPointF:
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return QPointF(v.x() * c - v.y() * s, v.x() * s + v.y() * c)

    # --- 状態の取得/復元(Undo 用) ---

    def state(self) -> tuple:
        return (self.pos(), self._rot, self._sx, self._sy)

    def restore_state(self, st: tuple) -> None:
        self.setPos(st[0])
        self._rot, self._sx, self._sy = st[1], st[2], st[3]
        self._apply_transform()
        self.sync_model()

    def sync_model(self) -> None:
        """アイテムの変形をモデルへ反映する"""
        self.region.pos = self.pos()
        self.region.rotation = self._rot
        self.region.scale_x = self._sx
        self.region.scale_y = self._sy

    def image_path(self) -> QPainterPath:
        """画像(シーン)座標での範囲パス"""
        return self.sceneTransform().map(self._local_path)

    # --- 描画 ---

    def boundingRect(self) -> QRectF:
        sx, sy = self._handle_scale
        mx = (ROTATE_OFFSET + HANDLE_SIZE) / sx
        my = (ROTATE_OFFSET + HANDLE_SIZE) / sy
        return self._local_path.boundingRect().adjusted(-mx, -my, mx, my)

    def shape(self) -> QPainterPath:
        p = QPainterPath(self._local_path)
        if not self.isSelected():
            return p
        # 当たり判定の広げた分もアイテムの形状に含める(ビュー側の判定用)。
        # addPath だと範囲とハンドルの重なりが奇偶規則で穴になるため united() で和集合にする
        handles = QPainterPath()
        for r in self._handle_rects(HANDLE_HIT_MARGIN).values():
            handles.addRect(r)
        handles.setFillRule(Qt.FillRule.WindingFill)
        return p.united(handles)

    def _handle_points(self) -> dict[str, QPointF]:
        """ローカル座標でのハンドル位置(四隅・各辺の中心・回転)"""
        br = self._local_path.boundingRect()
        cx, cy = br.center().x(), br.center().y()
        return {
            "tl": QPointF(br.left(), br.top()),
            "tr": QPointF(br.right(), br.top()),
            "bl": QPointF(br.left(), br.bottom()),
            "br": QPointF(br.right(), br.bottom()),
            "t": QPointF(cx, br.top()),
            "b": QPointF(cx, br.bottom()),
            "l": QPointF(br.left(), cy),
            "r": QPointF(br.right(), cy),
            "rotate": QPointF(cx, br.top() - self._rotate_offset()),
        }

    def _rotate_offset(self) -> float:
        """回転ハンドルの枠上辺からの距離(ローカル座標)

        基本は画面上一定だが、縮小して枠が小さく見えるときは表示高さに応じて短くする。
        """
        sy = self._handle_scale[1]
        screen_h = self._local_path.boundingRect().height() * sy
        offset = min(ROTATE_OFFSET, max(ROTATE_OFFSET_MIN, screen_h * ROTATE_OFFSET_RATIO))
        return offset / sy

    def _anchor_point(self, handle: str) -> QPointF:
        """リサイズ時に固定する側(ドラッグするハンドルの反対側)の点"""
        return self._handle_points()[OPPOSITE_HANDLE[handle]]

    def _handle_rects(self, margin: float = 0.0) -> dict[str, QRectF]:
        """ローカル座標でのハンドル矩形。倍率に合わせ見た目サイズを一定に保つ"""
        sx, sy = self._handle_scale
        w = (HANDLE_SIZE + margin * 2) / sx
        h = (HANDLE_SIZE + margin * 2) / sy
        return {
            name: QRectF(pt.x() - w / 2, pt.y() - h / 2, w, h)
            for name, pt in self._handle_points().items()
        }

    def _hit_handle(self, pos: QPointF) -> str | None:
        # 見た目どおりの位置で掴めるよう、当たり判定は少し広めに取る
        # (辞書順で四隅が辺の中央より優先される)
        for name, r in self._handle_rects(HANDLE_HIT_MARGIN).items():
            if r.contains(pos):
                return name
        return None

    def paint(self, painter, option, widget=None):
        if self._preview:
            return
        pen = QPen(QColor(255, 60, 60), 0)  # 幅 0 = ズームに影響されない線
        if self.isSelected():
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPath(self._local_path)
        if not self.isSelected():
            return
        # 選択中: 枠・四隅ハンドル(四角)・回転ハンドル(丸)を描く
        handles = self._handle_rects()
        br = self._local_path.boundingRect()
        painter.drawRect(br)
        painter.drawLine(
            QPointF(br.center().x(), br.top()), handles["rotate"].center()
        )
        painter.setBrush(QColor(255, 255, 255))
        for name, r in handles.items():
            if name == "rotate":
                painter.drawEllipse(r)
            else:
                painter.drawRect(r)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.changed.emit()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self._press_state = self.state()
        if self.isSelected() and event.button() == Qt.MouseButton.LeftButton:
            handle = self._hit_handle(event.pos())
            if handle == "rotate":
                self._drag_mode = "rotate"
                v = event.scenePos() - self.mapToScene(self._origin)
                self._grab_angle = math.degrees(math.atan2(v.y(), v.x())) - self._rot
                event.accept()
                return
            if handle is not None:
                self._drag_mode = "resize"
                self.begin_resize(handle, event.scenePos())
                event.accept()
                return
        self._drag_mode = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_mode == "rotate":
            v = event.scenePos() - self.mapToScene(self._origin)
            self._rot = math.degrees(math.atan2(v.y(), v.x())) - self._grab_angle
            self._apply_transform()
            return
        if self._drag_mode == "resize":
            self._resize_to(event.scenePos(), event.modifiers())
            return
        super().mouseMoveEvent(event)

    def begin_resize(self, handle: str, scene_pos: QPointF) -> None:
        """ハンドルのリサイズを開始する(掴んだ時点の状態を記録)"""
        anchor = self._anchor_point(handle) - self._origin
        self._resize = ResizeGrab(
            grab_pos=scene_pos,
            pos0=self.pos(),
            span=self._handle_points()[handle] - self._origin - anchor,
            anchor=anchor,
            sx0=self._sx,
            sy0=self._sy,
        )

    def _resize_to(self, scene_pos: QPointF, modifiers) -> None:
        """アンカー側を固定したまま、カーソルの移動量ぶんだけ拡縮する"""
        g = self._resize
        # カーソルの移動量を範囲のローカル軸(回転を戻した向き)に分解する
        d = self._rotate_vec(scene_pos - g.grab_pos, -self._rot)
        # 固定側からハンドルまでの距離 span が倍率 1.0 分に相当する
        sx = g.sx0 if not g.span.x() else g.sx0 + d.x() / g.span.x()
        sy = g.sy0 if not g.span.y() else g.sy0 + d.y() / g.span.y()
        if g.span.x() and g.span.y() and modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Shift 併用時は縦横比を保つ(変化量の大きい方に合わせる)
            f = max(sx / g.sx0, sy / g.sy0)
            sx, sy = g.sx0 * f, g.sy0 * f
        self._sx = max(sx, MIN_SCALE)
        self._sy = max(sy, MIN_SCALE)
        self._apply_transform(notify=False)
        # 拡縮は中心原点で効くため、固定側が動かないよう位置をずらす
        back = QPointF(
            (g.sx0 - self._sx) * g.anchor.x(), (g.sy0 - self._sy) * g.anchor.y()
        )
        self.setPos(g.pos0 + self._rotate_vec(back, self._rot))

    def mouseReleaseEvent(self, event):
        if self._drag_mode is not None:
            self._drag_mode = None
            self.sync_model()
            if self._press_state is not None and self._press_state != self.state():
                self.edited.emit(self, self._press_state)
            self._press_state = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self.sync_model()
        if self._press_state is not None and self._press_state != self.state():
            self.edited.emit(self, self._press_state)
        self._press_state = None


class MosaicCanvas(QGraphicsView):
    """画像と範囲を表示・編集するビュー"""

    regions_changed = Signal()  # 範囲の追加/削除/変形の通知(未保存フラグ用)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        # 右ドラッグをスクロールに使うためコンテキストメニューは出さない
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._overlay: MosaicOverlay | None = None
        self._image: Image.Image | None = None
        self._mode = ToolMode.RECT
        self._block = 10
        self._threshold = 0.0  # マス単位判定のしきい値(被覆率 0.0〜1.0)
        self._preview = False  # プレビュー(アウトライン非表示)中か
        self._loading = False
        self._undo_stack: list[tuple] = []
        self._panning = False
        self._pan_start = QPointF()
        self._pan_rest = QPointF()  # スクロール量の端数(切り捨て分の持ち越し)
        self._fit_pending = False  # 表示後にウィンドウ幅へフィットし直すか
        # 矩形作成用
        self._rect_start: QPointF | None = None
        self._temp_rect_item = None
        # ペン作成用
        self._pen_width = 20.0
        self._pen_points: list[QPointF] = []
        self._temp_path_item = None

    # --- 画像と範囲の管理 ---

    def set_image(self, img: Image.Image, regions: list[Region] | None = None) -> None:
        """画像と保存済み範囲を表示する(以前の内容はクリア)"""
        self._reset_scene()
        self._image = img
        pm = pil_to_qpixmap(img)
        self._pixmap_item = self._scene.addPixmap(pm)
        self._scene.setSceneRect(QRectF(pm.rect()))
        self._overlay = MosaicOverlay(QRectF(pm.rect()))
        self._overlay.setZValue(1)
        self._scene.addItem(self._overlay)
        self._rebuild_mosaic()
        for region in regions or []:
            self.add_region(region, push_undo=False)
        self._loading = False
        self._refresh_overlay()
        self._fit_to_window()

    def _reset_scene(self) -> None:
        """シーンの内容と作成中の一時状態を破棄する(画像の切替・クリアの共通処理)

        _loading は立てたまま返す(呼び出し側が表示を整えてから下ろす)。
        作成中の一時アイテムは _scene.clear() で Qt 側が破棄されるため、
        参照を残すと以降のドラッグ処理が解放済みアイテムを触ってしまう。
        """
        self._loading = True
        self._undo_stack.clear()
        self._pixmap_item = None
        self._overlay = None
        self._rect_start = None
        self._temp_rect_item = None
        self._pen_points = []
        self._temp_path_item = None
        if self._panning:
            self._panning = False
            self.unsetCursor()
        self._scene.clear()

    def clear_image(self) -> None:
        """表示中の画像と範囲を破棄する(ドロップ案内の表示状態へ戻す)"""
        self._reset_scene()
        self._image = None
        self._scene.setSceneRect(QRectF())
        self._loading = False
        self.viewport().update()

    def _fit_to_window(self) -> None:
        """画像全体がウィンドウに収まるよう表示倍率を合わせる"""
        if self._pixmap_item is None:
            return
        self.resetTransform()
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        # 表示前はビューポートサイズが確定しないため、表示後に再適用する
        self._fit_pending = not self.isVisible()
        self._sync_view_scale()

    def _sync_view_scale(self) -> None:
        """表示倍率を各範囲へ伝え、ハンドルを画面上一定サイズに保つ"""
        scale = self.transform().m11()
        for item in self._region_items():
            item.set_view_scale(scale)

    def showEvent(self, event):
        super().showEvent(event)
        if self._fit_pending:
            self._fit_to_window()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_pending:
            self._fit_to_window()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap_item is None:
            self._paint_placeholder()

    def _paint_placeholder(self) -> None:
        """画像未読み込み時に、ドロップを促す案内をビューポート中央へ描く"""
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.viewport().rect()
        color = self.palette().color(QPalette.ColorRole.WindowText)
        m = PLACEHOLDER_FRAME_MARGIN
        # 破線枠はドロップ可能な領域を示す
        pen = QPen(with_alpha(color, PLACEHOLDER_FRAME_ALPHA))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(
            rect.adjusted(m, m, -m, -m),
            PLACEHOLDER_FRAME_RADIUS, PLACEHOLDER_FRAME_RADIUS,
        )

        title_font = QFont(painter.font())
        title_font.setPointSize(PLACEHOLDER_TITLE_SIZE)
        title_font.setBold(True)
        hint_font = QFont(painter.font())
        hint_font.setPointSize(PLACEHOLDER_HINT_SIZE)
        title_h = QFontMetrics(title_font).height()
        hint_h = QFontMetrics(hint_font).height()
        # 見出しと補足を合わせた高さの中心を、ビューポート中央に合わせる
        total_h = title_h + PLACEHOLDER_LINE_GAP + hint_h
        top = rect.center().y() - total_h / 2
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter

        painter.setPen(with_alpha(color, PLACEHOLDER_TITLE_ALPHA))
        painter.setFont(title_font)
        painter.drawText(
            QRectF(rect.left(), top, rect.width(), title_h), align, PLACEHOLDER_TITLE
        )
        painter.setPen(with_alpha(color, PLACEHOLDER_HINT_ALPHA))
        painter.setFont(hint_font)
        painter.drawText(
            QRectF(
                rect.left(), top + title_h + PLACEHOLDER_LINE_GAP, rect.width(), hint_h
            ),
            align,
            PLACEHOLDER_HINT,
        )

    def set_mode(self, mode: ToolMode) -> None:
        # 範囲アイテムはどのモードでも選択・移動・変形できる(作成は空き領域のドラッグ)
        self._mode = mode

    def set_pen_width(self, width: float) -> None:
        self._pen_width = width

    def set_block_size(self, block: int) -> None:
        self._block = block
        self._rebuild_mosaic()
        # クリップ形状もマス目に依存するため作り直す
        self._update_clip()

    def set_threshold(self, ratio: float) -> None:
        """マス単位判定のしきい値(被覆率 0.0〜1.0)を設定する"""
        self._threshold = ratio
        self._update_clip()

    def set_preview_mode(self, on: bool) -> None:
        """プレビュー表示の切替(アウトラインを隠し、範囲の作成・編集も止める)"""
        self._preview = on
        if on:
            self._scene.clearSelection()
        for item in self._region_items():
            item.set_preview(on)

    def add_region(self, region: Region, push_undo: bool = True) -> RegionItem:
        item = RegionItem(region)
        item.setZValue(2)
        item.set_view_scale(self.transform().m11())
        item.set_preview(self._preview)
        item.changed.connect(self._refresh_overlay)
        item.edited.connect(
            lambda it, st: self._undo_stack.append(("transform", it, st))
        )
        self._scene.addItem(item)
        if push_undo:
            self._undo_stack.append(("add", item))
        self._refresh_overlay()
        return item

    def add_regions(self, regions: list[Region]) -> list[RegionItem]:
        """複数の範囲をまとめて追加する(自動検出用)

        Undo スタックには 1 エントリだけ積み、Ctrl+Z 一回で追加分をまとめて
        取り消せるようにする。追加分は非選択のまま置く(選択したままだと
        次の操作で自動検出の結果を丸ごと動かしてしまう)。
        """
        if not regions:
            return []
        self._scene.clearSelection()
        items = [self.add_region(region, push_undo=False) for region in regions]
        self._undo_stack.append(("add_many", items))
        return items

    def image_paths(self) -> list[QPainterPath]:
        """全範囲の画像座標パス(保存時のマスク生成に使う)"""
        return [item.image_path() for item in self._region_items()]

    def get_regions(self) -> list[Region]:
        """モデルを同期して全範囲を返す(画像切替時の保持用)"""
        items = self._region_items()
        for item in items:
            item.sync_model()
        return [item.region for item in items]

    def undo(self) -> None:
        """直前の操作(追加/削除/変形)を取り消す"""
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        if entry[0] == "add":
            self._scene.removeItem(entry[1])
        elif entry[0] == "add_many":
            for item in entry[1]:
                self._scene.removeItem(item)
        elif entry[0] == "remove":
            for item in entry[1]:
                self._scene.addItem(item)
        elif entry[0] == "transform":
            entry[1].restore_state(entry[2])
        self._refresh_overlay()

    def _region_items(self) -> list[RegionItem]:
        return [it for it in self._scene.items() if isinstance(it, RegionItem)]

    def _rebuild_mosaic(self) -> None:
        """モザイクサイズ変更・画像切替時にプレビュー元画像を作り直す"""
        if self._image is None or self._overlay is None:
            return
        self._overlay.set_mosaic(pil_to_qpixmap(make_mosaic_image(self._image, self._block)))

    def _refresh_overlay(self) -> None:
        self._update_clip()
        if not self._loading:
            self.regions_changed.emit()

    def _update_clip(self) -> None:
        """範囲パスをマス目にスナップした形状でオーバーレイをクリップする

        保存時と同じ mask_to_cell_grid を通すため、プレビューと保存結果が一致する。
        """
        if self._overlay is None or self._image is None:
            return
        combined = QPainterPath()
        for item in self._region_items():
            # addPath だと重なりが奇偶規則で打ち消し合うため united() で和集合にする
            combined = combined.united(item.image_path())
        if combined.isEmpty() or self._block <= 1:
            self._overlay.set_clip(combined)
            return
        mask = paths_to_mask([combined], self._image.size)
        grid = mask_to_cell_grid(mask, self._block, self._threshold)
        snapped = QPainterPath()
        for x, y, w, h in cell_grid_to_rects(grid, self._image.size):
            snapped.addRect(x, y, w, h)
        self._overlay.set_clip(snapped)

    # --- 入力 ---

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            items = [
                it for it in self._scene.selectedItems() if isinstance(it, RegionItem)
            ]
            if items:
                for it in items:
                    self._scene.removeItem(it)
                # 参照を保持したままスタックへ(Undo で戻せるようにする)
                self._undo_stack.append(("remove", items))
                self._refresh_overlay()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        # ホイールは常にズーム(スクロールは中ボタン/選択モードのドラッグで行う)
        zoom_in = event.angleDelta().y() > 0
        current = self.transform().m11()
        # フィット表示で既に範囲外の倍率になっていることがあるため、
        # その場合は倍率を跳ねさせずその向きのズームだけを止める
        target = current
        if zoom_in and current < VIEW_SCALE_MAX:
            target = min(current * ZOOM_STEP, VIEW_SCALE_MAX)
        elif not zoom_in and current > VIEW_SCALE_MIN:
            target = max(current / ZOOM_STEP, VIEW_SCALE_MIN)
        factor = target / current if current > 0 else 1.0
        if factor != 1.0:
            self.scale(factor, factor)
            self._sync_view_scale()
        event.accept()

    def mousePressEvent(self, event):
        # 右/中ボタンドラッグでスクロール
        if event.button() in (
            Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton
        ):
            self._panning = True
            self._pan_start = event.position()
            self._pan_rest = QPointF()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        # 既存範囲の上を押したときは、モードによらず選択・移動・変形を優先する
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._region_item_at(event.position().toPoint())
        ):
            super().mousePressEvent(event)
            return
        # 画像の外を押したときは範囲を作らない(選択解除だけを通す)
        can_create = (
            event.button() == Qt.MouseButton.LeftButton
            and not self._preview
            and self._is_on_image(event.position().toPoint())
        )
        # 矩形モード: ドラッグで矩形範囲を作成
        if can_create and self._mode is ToolMode.RECT:
            self._scene.clearSelection()
            self._rect_start = self.mapToScene(event.position().toPoint())
            pen = QPen(QColor(255, 60, 60), 0, Qt.PenStyle.DashLine)
            self._temp_rect_item = self._scene.addRect(
                QRectF(self._rect_start, self._rect_start), pen
            )
            event.accept()
            return
        # ペンモード: ドラッグの軌跡で範囲を作成
        if can_create and self._mode is ToolMode.PEN:
            self._scene.clearSelection()
            start = self.mapToScene(event.position().toPoint())
            self._pen_points = [start]
            pen = QPen(
                QColor(255, 60, 60, 140),
                self._pen_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            path = QPainterPath(start)
            path.lineTo(start)
            self._temp_path_item = self._scene.addPath(path, pen)
            event.accept()
            return
        super().mousePressEvent(event)

    def _region_item_at(self, view_pos) -> bool:
        """ビュー座標に範囲アイテムがあるか"""
        return any(isinstance(it, RegionItem) for it in self.items(view_pos))

    def _is_on_image(self, view_pos) -> bool:
        """ビュー座標が画像の内側か(画像未読み込みなら常に False)"""
        if self._pixmap_item is None:
            return False
        return self._pixmap_item.sceneBoundingRect().contains(self.mapToScene(view_pos))

    def mouseMoveEvent(self, event):
        if self._panning:
            # スクロール量は整数のため、切り捨てた端数を持ち越してカーソルと 1:1 に保つ
            delta = event.position() - self._pan_start + self._pan_rest
            dx, dy = int(delta.x()), int(delta.y())
            self._pan_rest = QPointF(delta.x() - dx, delta.y() - dy)
            self._pan_start = event.position()
            hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
            hbar.setValue(hbar.value() - dx)
            vbar.setValue(vbar.value() - dy)
            event.accept()
            return
        if self._rect_start is not None and self._temp_rect_item is not None:
            cur = self.mapToScene(event.position().toPoint())
            self._temp_rect_item.setRect(QRectF(self._rect_start, cur).normalized())
            event.accept()
            return
        if self._pen_points and self._temp_path_item is not None:
            cur = self.mapToScene(event.position().toPoint())
            self._pen_points.append(cur)
            path = QPainterPath(self._pen_points[0])
            for pt in self._pen_points[1:]:
                path.lineTo(pt)
            self._temp_path_item.setPath(path)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() in (
            Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton
        ):
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        if self._rect_start is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = QRectF(
                self._rect_start, self.mapToScene(event.position().toPoint())
            ).normalized()
            self._scene.removeItem(self._temp_rect_item)
            self._temp_rect_item = None
            self._rect_start = None
            # 誤クリックによる極小矩形は無視する
            if rect.width() >= 3 and rect.height() >= 3:
                region = Region(
                    kind=RegionKind.RECT,
                    rect=QRectF(0, 0, rect.width(), rect.height()),
                    pos=rect.topLeft(),
                )
                # 作成直後は選択状態にして、そのまま移動・変形できるようにする
                self.add_region(region).setSelected(True)
            event.accept()
            return
        if self._pen_points and event.button() == Qt.MouseButton.LeftButton:
            self._scene.removeItem(self._temp_path_item)
            self._temp_path_item = None
            points = self._pen_points
            self._pen_points = []
            region = Region(
                kind=RegionKind.STROKE, points=points, pen_width=self._pen_width
            )
            self.add_region(region).setSelected(True)
            event.accept()
            return
        super().mouseReleaseEvent(event)
