"""動画モードのタイムラインウィンドウ(カテゴリ別の行と区間バー)"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from mosaic_tool.regions import Region
from mosaic_tool.video.lanes import CATEGORY_LABELS, TimelineLane, build_rows
from mosaic_tool.video.session import VideoRegion

# レイアウト定数 (px)
LABEL_W = 72   # 左端のカテゴリラベル列(スクロールに追従して固定表示)
RULER_H = 20   # 上端のルーラー
ROW_H = 18     # 行の高さ
ROW_GAP = 2    # 行間
TAIL_W = 40    # 末尾フレームの後ろに残す余白(最後のバーを掴みやすくする)

# ズームの範囲 (px/フレーム) と 1 ノッチの倍率
ZOOM_MIN = 0.05
ZOOM_MAX = 20.0
ZOOM_STEP = 1.25

# 目盛りのラベルをおおよそこの間隔で置く (px)
TICK_LABEL_PX = 80

# 副目盛りの最小間隔 (px)。これより詰まる分割は使わない
MIN_MINOR_PX = 8
# 副目盛りの分割数(細かい順に試す)
_MINOR_DIVISORS = (10, 5, 2)

# バーの端をつかめる判定幅 (px / 片側)
HANDLE_PX = 5

# 配色: 動画編集ツールのタイムラインに合わせたダーク基調。
# 余白 < 行 の明るさにして行の境目が見えるようにし、バーと文字は明るい色を載せる
_BG = QColor(0x25, 0x25, 0x25)          # ウィンドウ全体と行間の余白
_ROW_BG = QColor(0x2E, 0x2E, 0x2E)      # 行の帯
_RULER_BG = QColor(0x33, 0x33, 0x33)    # 上端のルーラー
_LABEL_BG = QColor(0x1C, 0x1C, 0x1C)    # 左端のカテゴリラベル列(地色より暗く沈める)
_DIVIDER_COLOR = QColor(0x40, 0x40, 0x40)  # ラベル列と本体の境界線
_SCROLL_HANDLE = QColor(0x55, 0x55, 0x55)  # スクロールバーのつまみ
_TICK_COLOR = QColor(0xA0, 0xA0, 0xA0)  # 目盛りとフレーム番号
_TEXT_COLOR = QColor(0xDC, 0xDC, 0xDC)  # カテゴリラベル
# バーは不透明。重なっても濃さが変わらず、区間の数を色で誤読しない
_BAR_COLOR = QColor(0x6E, 0x9E, 0xD8)
_SELECTED_COLOR = QColor(0x4D, 0xA3, 0xFF)
_HANDLE_COLOR = QColor(0xFF, 0xFF, 0xFF)
_PLAYHEAD_COLOR = QColor(0xFF, 0x50, 0x50)
_GRID_MAJOR = QColor(0x3C, 0x3C, 0x3C)  # 主目盛りの縦線
_GRID_MINOR = QColor(0x33, 0x33, 0x33)  # 副目盛りの縦線


def _tick_interval(px_per_frame: float) -> int:
    """ラベルが TICK_LABEL_PX 前後の間隔になる目盛り幅(1/2/5×10^n 系列)"""
    target = max(1.0, TICK_LABEL_PX / max(px_per_frame, ZOOM_MIN))
    step = 1
    while step < target:
        for factor in (2, 5, 10):
            if step * factor >= target:
                return step * factor
        step *= 10
    return step


def _minor_interval(major: int, px_per_frame: float) -> int:
    """主目盛りを分割した副目盛りの幅

    割り切れて、かつ間隔が MIN_MINOR_PX 以上になる最も細かい分割を選ぶ。
    分割できなければ major をそのまま返す(この場合は副目盛りを描かない)。
    """
    for divisor in _MINOR_DIVISORS:
        if major % divisor:
            continue
        minor = major // divisor
        if minor * px_per_frame >= MIN_MINOR_PX:
            return minor
    return major


class TimelineArea(QWidget):
    """タイムライン本体。ルーラー・カテゴリ行・区間バー・再生ヘッドを自前描画する"""

    seek_requested = Signal(int)              # ルーラーのクリック/ドラッグ
    interval_edited = Signal(object, int, int)  # (Region, 開始, 終了) ドラッグ中に逐次
    region_clicked = Signal(object, int)      # (Region, クリック位置のフレーム)
    delete_requested = Signal(object)         # (Region) 選択中バーの削除要求
    scroll_requested = Signal(int)            # ズーム時のアンカー補正スクロール量
    hscroll_requested = Signal(int)           # ホイールによる横スクロール量(相対 px)
    playback_toggle_requested = Signal()      # Space による再生/一時停止の要求

    def __init__(self, parent=None):
        super().__init__(parent)
        # キーで削除できるようクリックでフォーカスを受け取る
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._total = 0
        self._frame = 0
        self._rows: list[TimelineLane] = []
        self._selected: Region | None = None
        self._px_per_frame = 2.0
        self._scroll_x = 0  # ラベル列の固定表示に使う水平スクロール量
        # ("start" | "end" | "move" | "seek", 対象). seek は対象なし
        self._drag: tuple[str, VideoRegion | None] | None = None
        self._grab_offset = 0  # move ドラッグでつかんだ位置と開始フレームの差

    # --- データ更新 ---

    def set_total(self, total: int) -> None:
        self._total = max(0, total)
        self._frame = min(self._frame, max(0, self._total - 1))
        self._apply_size()

    def set_data(
        self, regions: list[VideoRegion], selected: Region | None
    ) -> None:
        """区間一覧と選択中の範囲を反映し、行構成を作り直す"""
        self._rows = build_rows(regions)
        self._selected = selected
        self._apply_size()

    def set_frame(self, frame: int) -> None:
        self._frame = frame
        self.update()

    def set_scroll_x(self, x: int) -> None:
        self._scroll_x = x
        self.update()

    # --- 座標変換とジオメトリ ---

    def _x(self, frame: int) -> float:
        return LABEL_W + frame * self._px_per_frame

    def _frame_at_raw(self, x: float) -> int:
        """クランプなしのフレーム番号(ドラッグのクランプを呼び出し側で行うため)"""
        return int((x - LABEL_W) // self._px_per_frame)

    def _frame_at(self, x: float) -> int:
        if self._total <= 0:
            return 0
        return max(0, min(self._total - 1, self._frame_at_raw(x)))

    def _row_top(self, lane_index: int) -> float:
        return RULER_H + ROW_GAP + lane_index * (ROW_H + ROW_GAP)

    def _bar_rect(self, lane_index: int, vr: VideoRegion) -> QRectF:
        x1 = self._x(vr.start)
        # 両端含みの区間なので終了フレームの右端まで塗る
        x2 = self._x(vr.end + 1)
        return QRectF(x1, self._row_top(lane_index), max(3.0, x2 - x1), ROW_H)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt のオーバーライド)
        width = LABEL_W + self._total * self._px_per_frame + TAIL_W
        height = self._row_top(len(self._rows))
        return QSize(int(width), int(height))

    def _apply_size(self) -> None:
        """内容に合わせて自身を伸縮する(スクロール領域の中身として使うため)"""
        self.resize(self.sizeHint())
        self.updateGeometry()
        self.update()

    def _zoom(self, factor: float) -> None:
        """px/フレームを factor 倍する(範囲外はクランプ)"""
        ppf = max(ZOOM_MIN, min(ZOOM_MAX, self._px_per_frame * factor))
        if ppf == self._px_per_frame:
            return
        self._px_per_frame = ppf
        self._apply_size()

    # --- ヒット判定 ---

    def _row_at(self, y: float) -> int | None:
        """y 座標に掛かる行の index(行の外なら None)"""
        for i in range(len(self._rows)):
            top = self._row_top(i)
            if top <= y <= top + ROW_H:
                return i
        return None

    def _edge_at(self, pos: QPointF) -> tuple[VideoRegion, str] | None:
        """選択中のバーの端 (±HANDLE_PX) をつかんでいればどちらの端かを返す"""
        if self._selected is None:
            return None
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._rows[row_index].items:
            if vr.region is not self._selected:
                continue
            bar = self._bar_rect(row_index, vr)
            d_start = abs(pos.x() - bar.left())
            d_end = abs(pos.x() - bar.right())
            # 短いバーでは判定幅を細めて、中央の平行移動をつかめる余地を残す
            handle = min(HANDLE_PX, bar.width() / 3)
            if min(d_start, d_end) > handle:
                return None
            return vr, ("start" if d_start <= d_end else "end")
        return None

    def _bar_at(self, pos: QPointF) -> VideoRegion | None:
        """座標に掛かる区間バーを返す(後に描いたものを優先)"""
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in reversed(self._rows[row_index].items):
            if self._bar_rect(row_index, vr).contains(pos):
                return vr
        return None

    # --- マウス・キー操作 ---

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if pos.y() < RULER_H:
            self._drag = ("seek", None)
            self.seek_requested.emit(self._frame_at(pos.x()))
            return
        edge = self._edge_at(pos)
        if edge is not None:
            self._drag = (edge[1], edge[0])
            return
        vr = self._bar_at(pos)
        if vr is None:
            self._drag = None
            return
        self._drag = ("move", vr)
        self._grab_offset = self._frame_at_raw(pos.x()) - vr.start
        self.region_clicked.emit(vr.region, self._frame_at(pos.x()))

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        kind, vr = self._drag
        x = event.position().x()
        if kind == "seek":
            self.seek_requested.emit(self._frame_at(x))
            return
        before = (vr.start, vr.end)
        if kind == "start":
            # 終了フレームを越えないようクランプする
            vr.start = max(0, min(self._frame_at_raw(x), vr.end))
        elif kind == "end":
            # 終了側は「バーの右端」をつかむため、境界の 1 つ手前が終了フレーム
            vr.end = max(vr.start, min(self._max_frame(), self._frame_at_raw(x) - 1))
        else:
            length = vr.end - vr.start
            start = self._frame_at_raw(x) - self._grab_offset
            vr.start = max(0, min(start, self._max_frame() - length))
            vr.end = vr.start + length
        if (vr.start, vr.end) != before:
            self.update()
            self.interval_edited.emit(vr.region, vr.start, vr.end)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def _max_frame(self) -> int:
        return max(0, self._total - 1)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.playback_toggle_requested.emit()
            event.accept()
            return
        if (
            event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and self._selected is not None
        ):
            self.delete_requested.emit(self._selected)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        """Ctrl+ホイールで横ズーム、修飾なしで横スクロール、Shift で縦スクロール"""
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            super().wheelEvent(event)
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120.0
            if notches == 0:
                super().wheelEvent(event)
                return
            cursor_x = event.position().x()
            frame = self._frame_at(cursor_x)
            # ズーム前のカーソル位置(ビューポート座標)を保ったままスクロールし直す
            viewport_x = cursor_x - self._scroll_x
            self._zoom(ZOOM_STEP**notches)
            self.scroll_requested.emit(int(self._x(frame) - viewport_x))
            event.accept()
            return
        # 横向きのホイールを持つデバイスでは x 側だけが動く
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            super().wheelEvent(event)
            return
        # 上回転(正)で左へ進めるため符号を反転する
        self.hscroll_requested.emit(-delta)
        event.accept()

    # --- 描画 ---

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(event.rect())
        painter.fillRect(rect, _BG)
        self._paint_rows(painter, rect)
        self._paint_grid(painter, rect)
        self._paint_bars(painter, rect)
        self._paint_ruler(painter, rect)
        self._paint_playhead(painter)
        self._paint_labels(painter)
        painter.end()

    RULER_MAJOR_TICK = 5  # 主目盛りの線の長さ (px)
    RULER_MINOR_TICK = 3  # 副目盛りの線の長さ (px)

    def _paint_ruler(self, painter: QPainter, rect: QRectF) -> None:
        """上端のルーラーに主副の目盛りとフレーム番号を描く"""
        painter.fillRect(QRectF(rect.left(), 0, rect.width(), RULER_H), _RULER_BG)
        major = _tick_interval(self._px_per_frame)
        minor = _minor_interval(major, self._px_per_frame)
        first = max(0, self._frame_at(rect.left()) // minor * minor)
        last = self._frame_at(rect.right())
        painter.setPen(_TICK_COLOR)
        for frame in range(first, last + 1, minor):
            x = int(self._x(frame))
            is_major = frame % major == 0
            length = self.RULER_MAJOR_TICK if is_major else self.RULER_MINOR_TICK
            painter.drawLine(x, RULER_H - length, x, RULER_H)
            if is_major:
                painter.drawText(x + 2, RULER_H - 7, str(frame))

    def _paint_rows(self, painter: QPainter, rect: QRectF) -> None:
        """各行の背景を描く"""
        for i in range(len(self._rows)):
            painter.fillRect(
                QRectF(rect.left(), self._row_top(i), rect.width(), ROW_H), _ROW_BG
            )

    def _paint_grid(self, painter: QPainter, rect: QRectF) -> None:
        """行エリアに縦線を引く(主目盛りは明るく、副目盛りは暗く)

        行背景の後・区間バーの前に描き、バーが線に埋もれないようにする。
        """
        bottom = self._row_top(len(self._rows))
        if bottom <= RULER_H + ROW_GAP:
            return
        major = _tick_interval(self._px_per_frame)
        minor = _minor_interval(major, self._px_per_frame)
        first = max(0, self._frame_at(rect.left()) // minor * minor)
        last = self._frame_at(rect.right())
        for frame in range(first, last + 1, minor):
            painter.setPen(_GRID_MAJOR if frame % major == 0 else _GRID_MINOR)
            x = int(self._x(frame))
            painter.drawLine(x, int(RULER_H), x, int(bottom))

    def _paint_bars(self, painter: QPainter, rect: QRectF) -> None:
        """可視フレーム範囲に掛かる区間バーを描く

        自動検出はフレームごとの区間で数千個になり得るため、
        描画対象を可視範囲に掛かるバーだけへ絞る。
        """
        left_frame = self._frame_at_raw(rect.left()) - 1
        right_frame = self._frame_at_raw(rect.right()) + 1
        painter.setPen(Qt.PenStyle.NoPen)
        for i, row in enumerate(self._rows):
            for vr in row.items:
                if vr.end < left_frame or vr.start > right_frame:
                    continue
                selected = self._selected is not None and vr.region is self._selected
                bar = self._bar_rect(i, vr)
                painter.setBrush(_SELECTED_COLOR if selected else _BAR_COLOR)
                painter.drawRect(bar)
                if selected:
                    # 端ハンドル(白い縦線)でドラッグできることを示す
                    painter.setBrush(_HANDLE_COLOR)
                    for x in (bar.left(), bar.right() - 3):
                        painter.drawRect(QRectF(x, bar.top(), 3, bar.height()))

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self._x(self._frame)
        painter.setPen(_PLAYHEAD_COLOR)
        painter.drawLine(int(x), 0, int(x), self.height())

    def _paint_labels(self, painter: QPainter) -> None:
        """スクロール位置に追従させたラベル列(見かけ上の固定表示)"""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_LABEL_BG)
        painter.drawRect(QRectF(self._scroll_x, 0, LABEL_W, self.height()))
        # 列の右端に境界線を引き、本体との境目をはっきりさせる
        painter.setPen(_DIVIDER_COLOR)
        x = self._scroll_x + LABEL_W
        painter.drawLine(x, 0, x, self.height())
        painter.setPen(_TEXT_COLOR)
        for i, row in enumerate(self._rows):
            # カテゴリの先頭行にだけラベルを出す(同カテゴリの 2 行目以降は空欄)
            if i > 0 and self._rows[i - 1].source is row.source:
                continue
            painter.drawText(
                QRectF(self._scroll_x + 4, self._row_top(i), LABEL_W - 8, ROW_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                CATEGORY_LABELS[row.source],
            )


class TimelineWindow(QWidget):
    """タイムラインウィンドウ本体。TimelineArea を横スクロール領域に載せる"""

    # TimelineArea の同名シグナルをそのまま中継する
    seek_requested = Signal(int)
    interval_edited = Signal(object, int, int)
    region_clicked = Signal(object, int)
    delete_requested = Signal(object)
    playback_toggle_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setWindowTitle("タイムライン")
        self.resize(900, 220)
        self._area = TimelineArea()
        self._scroll = QScrollArea()
        # タイムラインより広い余白(内容がビューポートに満たない部分)と
        # スクロールバーも同じ地色にして、明るい既定色が浮かないようにする
        self._scroll.setStyleSheet(
            f"QScrollArea, QScrollArea > QWidget > QWidget "
            f"{{ background: {_BG.name()}; border: 0; }}"
            f"QScrollBar:horizontal, QScrollBar:vertical "
            f"{{ background: {_BG.name()}; border: 0; }}"
            f"QScrollBar::handle {{ background: {_SCROLL_HANDLE.name()}; }}"
            f"QScrollBar::add-line, QScrollBar::sub-line "
            f"{{ width: 0; height: 0; }}"
        )
        # sizeHint の幅(フレーム数 × px/フレーム)をそのまま使うため自動伸縮はしない
        self._scroll.setWidgetResizable(False)
        self._scroll.setWidget(self._area)
        self._scroll.horizontalScrollBar().valueChanged.connect(
            self._area.set_scroll_x
        )
        self._area.seek_requested.connect(self.seek_requested)
        self._area.interval_edited.connect(self.interval_edited)
        self._area.region_clicked.connect(self.region_clicked)
        self._area.delete_requested.connect(self.delete_requested)
        self._area.scroll_requested.connect(self._scroll_to)
        self._area.hscroll_requested.connect(self._scroll_by)
        self._area.playback_toggle_requested.connect(self.playback_toggle_requested)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    def _scroll_to(self, x: int) -> None:
        """ズームのアンカー補正(カーソル下のフレームを画面上に留める)"""
        self._scroll.horizontalScrollBar().setValue(x)

    def _scroll_by(self, delta: int) -> None:
        """ホイールによる横スクロール(現在位置からの相対移動)"""
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.value() + delta)

    def set_total(self, total: int) -> None:
        self._area.set_total(total)

    def set_data(
        self, regions: list[VideoRegion], selected: Region | None
    ) -> None:
        self._area.set_data(regions, selected)

    def set_frame(self, frame: int) -> None:
        """再生ヘッドを移動し、可視範囲から外れたらスクロールで追従する"""
        self._area.set_frame(frame)
        x = self._area._x(frame)
        bar = self._scroll.horizontalScrollBar()
        view_w = self._scroll.viewport().width()
        if not (bar.value() + LABEL_W <= x <= bar.value() + view_w):
            bar.setValue(int(x - view_w * 0.2))
