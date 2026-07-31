"""動画モードのタイムラインウィンドウ(カテゴリ別の行と区間バー)"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from mosaic_tool.regions import Region
from mosaic_tool.video.lanes import (
    CATEGORY_LABELS,
    TimelineLane,
    build_rows,
    clamp_lane_delta,
)
from mosaic_tool.video.session import VideoRegion
from mosaic_tool.video.timeline_selection import (
    END,
    MOVE,
    START,
    TimelineSelection,
    apply_delta,
    clamp_delta,
)

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

# ラバーバンド(矩形選択)を表す _drag の種類
RUBBER = "rubber"

# 右下に重ねるキーガイドの本文と、ビューポート端からの余白 (px)
KEY_GUIDE_LINES = (
    "Space: 再生 / 一時停止",
    "← / →: 1 フレーム移動",
    "ホイール: 横スクロール",
    "Shift + ホイール: 縦スクロール",
    "Ctrl + ホイール: 拡大 / 縮小",
    "ドラッグ: 区間の移動(端で伸縮)",
    "Delete: 選択中の区間を削除",
)
KEY_GUIDE_MARGIN = 8

# バー端の縁取りの幅 (px)。選択中は太くして掴める位置を示す
BAR_EDGE_W = 1
SELECTED_EDGE_W = 2
# これより細いバーには縁取りを描かない(縁で埋まって区間が見えなくなる)。
# _bar_rect が幅を最低 3px に広げるため、それより大きい値にする
MIN_EDGE_BAR_W = 5.0

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
_BAR_EDGE = QColor(0xC3, 0xDB, 0xF5)       # バー端の縁取り(区間の境目を見せる)
_BAR_DIM = QColor(0x44, 0x5A, 0x74)        # 選択があるときの非選択バー
_SELECTED_EDGE = QColor(0xFF, 0xFF, 0xFF)  # 選択中バーの縁取り
_RUBBER_FILL = QColor(0x4D, 0xA3, 0xFF, 0x40)  # 矩形選択の塗り(下が見える半透明)
_RUBBER_LINE = QColor(0xCC, 0xDD, 0xFF)    # 矩形選択の枠線
_PLAYHEAD_COLOR = QColor(0xFF, 0x50, 0x50)
_GRID_MAJOR = QColor(0x3C, 0x3C, 0x3C)  # 主目盛りの縦線
_GRID_MINOR = QColor(0x33, 0x33, 0x33)  # 副目盛りの縦線
_GUIDE_BG = QColor(0x18, 0x18, 0x18, 0xC8)  # キーガイドの下地(下のバーが薄く透ける)
_GUIDE_TEXT = QColor(0x9E, 0x9E, 0x9E)      # キーガイドの文字(主役より沈める)


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
    intervals_edited = Signal()               # 区間が変わった(ドラッグ中に逐次)
    delete_requested = Signal(list)           # ([Region]) 選択中バーの削除要求
    selection_changed = Signal(list)          # 選択が変わった([Region])
    scroll_requested = Signal(int)            # ズーム時のアンカー補正スクロール量
    hscroll_requested = Signal(int)           # ホイールによる横スクロール量(相対 px)
    playback_toggle_requested = Signal()      # Space による再生/一時停止の要求
    step_requested = Signal(int)              # ←/→ によるコマ送り(相対フレーム数)

    def __init__(self, parent=None):
        super().__init__(parent)
        # ←/→ や Space をこのウィジェットで受けるため、常にフォーカスを持てるようにする
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # ボタンを押していない移動でもカーソル形状を切り替えるため必要
        self.setMouseTracking(True)
        self._total = 0
        self._frame = 0
        self._rows: list[TimelineLane] = []
        # ドラッグ中に行を組み直すため、受け取った区間そのものを持つ
        self._regions: list[VideoRegion] = []
        self._selection = TimelineSelection()
        self._px_per_frame = 2.0
        self._scroll_x = 0  # ラベル列の固定表示に使う水平スクロール量
        self._view_h = 0    # ビューポートの高さ(行が少なくてもここまで描く)
        # ("start" | "end" | "move" | "seek", 対象). seek は対象なし
        self._drag: tuple[str, VideoRegion | None] | None = None
        self._grab_offset = 0  # move ドラッグでつかんだ位置と開始フレームの差
        self._drag_items: list[VideoRegion] = []  # ドラッグ開始時の選択(一括編集の対象)
        # ラバーバンドの始点・終点(ウィジェット座標)と、加算開始時の元の選択
        self._rubber_origin = QPointF()
        self._rubber_end = QPointF()
        self._rubber_base: list[VideoRegion] = []

    # --- データ更新 ---

    def set_total(self, total: int) -> None:
        self._total = max(0, total)
        self._frame = min(self._frame, max(0, self._total - 1))
        self._apply_size()

    def set_data(self, regions: list[VideoRegion]) -> None:
        """区間一覧を反映し、行構成を作り直す(消えた区間は選択から落とす)"""
        self._regions = list(regions)
        self._selection.prune(regions)
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        """保持している区間から行構成を作り直して描き直す"""
        self._rows = build_rows(self._regions)
        self._apply_size()

    def set_selection(self, regions: list[Region]) -> None:
        """外部(キャンバス)の選択を反映する

        自分が発端ではないため selection_changed は出さない。出すと app 側で
        キャンバスとの同期が往復してしまう。
        """
        targets = {id(r) for r in regions}
        self._selection.replace([
            vr
            for row in self._rows
            for vr in row.items
            if id(vr.region) in targets
        ])
        self.update()

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self._selection.regions())

    def set_frame(self, frame: int) -> None:
        self._frame = frame
        self.update()

    def set_scroll_x(self, x: int) -> None:
        self._scroll_x = x
        self.update()

    def set_viewport_height(self, height: int) -> None:
        """ビューポートの高さを受け取り、行が足りなくてもそこまで背景を描く"""
        height = max(0, height)
        if height == self._view_h:
            return
        self._view_h = height
        self._apply_size()

    # --- 座標変換とジオメトリ ---

    def frame_x(self, frame: int) -> float:
        """フレーム番号に対応する x 座標(スクロール追従のため親からも使う)"""
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
        x1 = self.frame_x(vr.start)
        # 両端含みの区間なので終了フレームの右端まで塗る
        x2 = self.frame_x(vr.end + 1)
        return QRectF(x1, self._row_top(lane_index), max(3.0, x2 - x1), ROW_H)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt のオーバーライド)
        width = LABEL_W + self._total * self._px_per_frame + TAIL_W
        # 行が少なくてもビューポートを埋め、地の色や目盛りが下端まで続くようにする
        height = max(self._row_top(len(self._rows)), self._view_h)
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

    def _row_at_nearest(self, y: float) -> int:
        """縦ドラッグ用に、y へ最も近い行 index を返す

        行間の余白でも落とし先が決まるよう丸める。末尾は行数そのものまで許し、
        一番下へ払ったときに新しい行を作れるようにする。
        """
        raw = int((y - RULER_H - ROW_GAP) // (ROW_H + ROW_GAP))
        return max(0, min(len(self._rows), raw))

    def _row_index_of(self, vr: VideoRegion) -> int:
        """その区間が載っている行 index"""
        for i, row in enumerate(self._rows):
            if any(v is vr for v in row.items):
                return i
        return 0

    def _category_span(self, source) -> tuple[int, int]:
        """カテゴリの先頭行 index と行数"""
        rows = [i for i, row in enumerate(self._rows) if row.source is source]
        return (rows[0], len(rows)) if rows else (0, 0)

    def _lane_of(self, vr: VideoRegion) -> int:
        """カテゴリ内のレーン番号(行 index からカテゴリの先頭分を引く)"""
        return self._row_index_of(vr) - self._category_span(vr.source)[0]

    def _hit_order(self, row_index: int) -> list[VideoRegion]:
        """当たり判定の走査順

        選択中のバーを先に見る。横に並んだバーの端が判定幅の中で競合しても、
        いま掴もうとしている選択中の端が勝つようにする。各群の中では後に
        描いたものを優先する(見えている方を掴む)。
        """
        items = list(reversed(self._rows[row_index].items))
        selected = [vr for vr in items if self._selection.contains(vr)]
        return selected + [
            vr for vr in items if not self._selection.contains(vr)
        ]

    def _edge_at(self, pos: QPointF) -> tuple[VideoRegion, str] | None:
        """バーの端 (±HANDLE_PX) をつかんでいればどちらの端かを返す

        選択の有無は問わない。掴んだ時点でそのバーを選択するため、選択と
        リサイズを 2 手に分けずに済む。
        """
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._hit_order(row_index):
            bar = self._bar_rect(row_index, vr)
            d_start = abs(pos.x() - bar.left())
            d_end = abs(pos.x() - bar.right())
            # 短いバーでは判定幅を細めて、中央の平行移動をつかめる余地を残す
            handle = min(HANDLE_PX, bar.width() / 3)
            if min(d_start, d_end) > handle:
                # 同じ行に並ぶ別のバーの端かもしれないので探し続ける
                continue
            return vr, (START if d_start <= d_end else END)
        return None

    def _bar_at(self, pos: QPointF) -> VideoRegion | None:
        """座標に掛かる区間バーを返す(選択中を優先し、次に後に描いたもの)"""
        row_index = self._row_at(pos.y())
        if row_index is None:
            return None
        for vr in self._hit_order(row_index):
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
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            # 修飾つきクリックは選択の出入りだけを行い、ドラッグは始めない
            vr = self._bar_at(pos)
            if vr is not None:
                self._drag = None
                self._selection.toggle(vr)
                self._emit_selection()
                self.update()
                return
            self._begin_rubber(pos, additive=True)
            return
        edge = self._edge_at(pos)
        if edge is not None:
            self._begin_edit(edge[1], edge[0], pos)
            return
        vr = self._bar_at(pos)
        if vr is None:
            self._begin_rubber(pos, additive=False)
            return
        self._begin_edit(MOVE, vr, pos)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            self._update_cursor(event.position())
            return
        kind, anchor = self._drag
        pos = event.position()
        if kind == "seek":
            self.seek_requested.emit(self._frame_at(pos.x()))
            return
        if kind == RUBBER:
            self._rubber_end = pos
            self._apply_rubber()
            self.update()
            return
        delta = clamp_delta(
            self._drag_items,
            kind,
            self._desired_delta(kind, anchor, pos.x()),
            self._max_frame(),
        )
        moved = delta != 0
        if moved:
            apply_delta(self._drag_items, kind, delta)
        # 平行移動のときだけ行もつかんで動かす(端の伸縮は横方向だけ)
        if kind == MOVE and self._apply_lane_drag(pos.y(), anchor):
            moved = True
        if not moved:
            return
        self.update()
        self.intervals_edited.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and self._drag[0] in (MOVE, START, END):
            self._fix_lanes(self._drag_items)
        self._drag = None
        self._drag_items = []

    def _fix_lanes(self, items: list[VideoRegion]) -> None:
        """手を離した時点の行を手動指定として固定する

        横移動やリサイズだけでも固定するのは、操作直後に自動配置でバーが
        別の行へ飛ぶのを防ぐため。押し出された区間は自動配置へ戻す。
        処理は「掴んだ区間の行を固定 → 被った区間の固定を解除 → 行の再構成」
        の 3 段階で進む。
        """
        if not items:
            return
        for vr in items:
            vr.lane = self._lane_of(vr)
        self._claim_lanes(items)
        self._rebuild_rows()

    def _begin_rubber(self, pos: QPointF, additive: bool) -> None:
        """空白からのドラッグで矩形選択を始める

        修飾なしなら元の選択を捨てる(空白クリックだけで選択解除になる)。
        """
        self._drag = (RUBBER, None)
        self._rubber_origin = pos
        self._rubber_end = pos
        self._rubber_base = self._selection.items() if additive else []
        if not additive and len(self._selection) > 0:
            self._selection.clear()
            self._emit_selection()
        self.update()

    def _rubber_rect(self) -> QRectF:
        """始点と終点から作る選択矩形

        真横や真下へ払うドラッグでは幅か高さが 0 になる。Qt は空の矩形の交差を
        常に偽と返すため、最低 1px の広がりを持たせて 1 行だけを払う操作を通す。
        """
        rect = QRectF(self._rubber_origin, self._rubber_end).normalized()
        if rect.width() < 1.0:
            rect.setWidth(1.0)
        if rect.height() < 1.0:
            rect.setHeight(1.0)
        return rect

    def _apply_rubber(self) -> None:
        """矩形と交差する区間バーを選択する(加算開始なら元の選択へ足す)"""
        rect = self._rubber_rect()
        hits = [
            vr
            for i, row in enumerate(self._rows)
            for vr in row.items
            if self._bar_rect(i, vr).intersects(rect)
        ]
        self._selection.replace(self._rubber_base + hits)
        self._emit_selection()

    def _begin_edit(self, kind: str, anchor: VideoRegion, pos: QPointF) -> None:
        """つかんだバーを起点に区間編集のドラッグを始める

        つかんだバーが選択外なら、そのバー 1 つだけを選び直す。選択内なら
        選択全体が対象になる。
        """
        if not self._selection.contains(anchor):
            self._selection.replace([anchor])
            self._emit_selection()
            self.update()
        self._drag = (kind, anchor)
        self._drag_items = self._selection.items()
        self._grab_offset = self._frame_at_raw(pos.x()) - anchor.start

    def _desired_delta(self, kind: str, anchor: VideoRegion, x: float) -> int:
        """つかんだバーの目標位置から、選択全体へ当てたい移動量を出す"""
        frame = self._frame_at_raw(x)
        if kind == MOVE:
            return frame - self._grab_offset - anchor.start
        if kind == START:
            return frame - anchor.start
        # 終了側は「バーの右端」をつかむため、境界の 1 つ手前が終了フレーム
        return frame - 1 - anchor.end

    def _apply_lane_drag(self, y: float, anchor: VideoRegion) -> bool:
        """縦ドラッグを選択全体の行移動へ変える(動かせたら True)"""
        items = self._drag_items
        lanes = [self._lane_of(vr) for vr in items]
        limits = [self._category_span(vr.source)[1] for vr in items]
        start, _ = self._category_span(anchor.source)
        wanted = self._row_at_nearest(y) - start - self._lane_of(anchor)
        delta = clamp_lane_delta(lanes, limits, wanted)
        if delta == 0:
            return False
        for vr, lane in zip(items, lanes):
            vr.lane = lane + delta
        self._claim_lanes(items)
        self._rebuild_rows()
        return True

    def _claim_lanes(self, items: list[VideoRegion]) -> None:
        """掴んでいる区間が取った行から、被る他区間の手動指定を外す

        後から来た操作を優先し、押し出された側は自動配置へ戻す。
        """
        moving = {id(vr) for vr in items}
        for vr in self._regions:
            if id(vr) in moving or vr.lane is None:
                continue
            if any(
                other.source is vr.source
                and other.lane == vr.lane
                and other.start <= vr.end
                and vr.start <= other.end
                for other in items
            ):
                vr.lane = None

    def _update_cursor(self, pos: QPointF) -> None:
        """カーソル位置に応じて形状を変え、できる操作を示す"""
        if pos.y() < RULER_H:
            self.unsetCursor()
        elif self._edge_at(pos) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._bar_at(pos) is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _max_frame(self) -> int:
        return max(0, self._total - 1)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.playback_toggle_requested.emit()
            event.accept()
            return
        # メインウィンドウと同じくコマ送りに割り当てる
        # (受け取らないとスクロール領域の横スクロールとして消費される)
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.step_requested.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
            return
        if (
            event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and len(self._selection) > 0
        ):
            self.delete_requested.emit(self._selection.regions())
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
            self.scroll_requested.emit(int(self.frame_x(frame) - viewport_x))
            event.accept()
            return
        # 横向きのホイールを持つデバイスでは x 側だけが動く
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            super().wheelEvent(event)
            return
        # 上回転(正)で右へ進める(縦スクロールとは逆向きにして時間軸を送る感覚に合わせる)
        self.hscroll_requested.emit(delta)
        event.accept()

    # --- 描画 ---

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(event.rect())
        painter.fillRect(rect, _BG)
        self._paint_rows(painter, rect)
        self._paint_grid(painter, rect)
        self._paint_bars(painter, rect)
        self._paint_rubber(painter)
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
            x = int(self.frame_x(frame))
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
        if not self._rows:
            return
        # 行の下にも縦線を伸ばし、行が少なくても下端まで時間軸が続いて見えるようにする
        bottom = self.height()
        major = _tick_interval(self._px_per_frame)
        minor = _minor_interval(major, self._px_per_frame)
        first = max(0, self._frame_at(rect.left()) // minor * minor)
        last = self._frame_at(rect.right())
        for frame in range(first, last + 1, minor):
            painter.setPen(_GRID_MAJOR if frame % major == 0 else _GRID_MINOR)
            x = int(self.frame_x(frame))
            painter.drawLine(x, int(RULER_H), x, int(bottom))

    def _paint_bars(self, painter: QPainter, rect: QRectF) -> None:
        """可視フレーム範囲に掛かる区間バーを描く

        自動検出はフレームごとの区間で数千個になり得るため、
        描画対象を可視範囲に掛かるバーだけへ絞る。
        """
        left_frame = self._frame_at_raw(rect.left()) - 1
        right_frame = self._frame_at_raw(rect.right()) + 1
        # 選択が 1 つ以上あるときだけ非選択バーを沈め、選択を浮き上がらせる
        dim = len(self._selection) > 0
        painter.setPen(Qt.PenStyle.NoPen)
        for i, row in enumerate(self._rows):
            for vr in row.items:
                if vr.end < left_frame or vr.start > right_frame:
                    continue
                selected = self._selection.contains(vr)
                bar = self._bar_rect(i, vr)
                if selected:
                    painter.setBrush(_SELECTED_COLOR)
                else:
                    painter.setBrush(_BAR_DIM if dim else _BAR_COLOR)
                painter.drawRect(bar)
                self._paint_bar_edges(painter, bar, selected)
                if selected:
                    self._paint_selected_outline(painter, bar)

    def _paint_bar_edges(
        self, painter: QPainter, bar: QRectF, selected: bool
    ) -> None:
        """バーの左右端に縁取り線を描き、区間の境目と掴める位置を示す"""
        if bar.width() < MIN_EDGE_BAR_W:
            return
        width = SELECTED_EDGE_W if selected else BAR_EDGE_W
        painter.setBrush(_SELECTED_EDGE if selected else _BAR_EDGE)
        painter.drawRect(QRectF(bar.left(), bar.top(), width, bar.height()))
        painter.drawRect(
            QRectF(bar.right() - width, bar.top(), width, bar.height())
        )

    def _paint_selected_outline(self, painter: QPainter, bar: QRectF) -> None:
        """選択中バーを白線で囲む

        色差だけでなく形でも分かるようにして、バーが密集した行でも見失わない。
        呼び出し後にペンとブラシを塗り用へ戻す。
        """
        painter.setPen(_SELECTED_EDGE)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # drawRect は右端と下端を線の分だけはみ出すため 1px 内側へ寄せる
        painter.drawRect(bar.adjusted(0, 0, -1, -1))
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_rubber(self, painter: QPainter) -> None:
        """ドラッグ中の矩形選択を描く(半透明の塗りと点線枠)"""
        if self._drag is None or self._drag[0] != RUBBER:
            return
        pen = QPen(_RUBBER_LINE)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(_RUBBER_FILL)
        painter.drawRect(self._rubber_rect())

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self.frame_x(self._frame)
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


class KeyGuide(QLabel):
    """操作キーの一覧をビューポート右下へ薄く重ねる札

    タイムライン上の操作を邪魔しないよう、マウスイベントは下へ素通しする。
    """

    def __init__(self, parent=None):
        super().__init__("\n".join(KEY_GUIDE_LINES), parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # 親(スクロール領域)のスタイルシートに飲まれないよう id で指定する
        self.setObjectName("keyGuide")
        background = (
            f"rgba({_GUIDE_BG.red()}, {_GUIDE_BG.green()},"
            f" {_GUIDE_BG.blue()}, {_GUIDE_BG.alpha()})"
        )
        self.setStyleSheet(
            "QLabel#keyGuide {"
            f" background: {background}; color: {_GUIDE_TEXT.name()};"
            " border-radius: 4px; padding: 6px 8px; }"
        )
        self.adjustSize()

    def reposition(self) -> None:
        """親の右下へ寄せ直す(ビューポートのリサイズごとに呼ぶ)"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        self.move(
            parent.width() - self.width() - KEY_GUIDE_MARGIN,
            parent.height() - self.height() - KEY_GUIDE_MARGIN,
        )
        self.raise_()


class TimelineWindow(QWidget):
    """タイムラインウィンドウ本体。TimelineArea を横スクロール領域に載せる"""

    # TimelineArea の同名シグナルをそのまま中継する
    seek_requested = Signal(int)
    intervals_edited = Signal()
    delete_requested = Signal(list)
    selection_changed = Signal(list)
    playback_toggle_requested = Signal()
    step_requested = Signal(int)
    closed = Signal()  # × で閉じられた(ツールバーのトグルを戻すため)

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
        # ←/→ をスクロール領域に横スクロールとして食われないよう、
        # キー入力は常に TimelineArea が受け取るようにする
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._guide = KeyGuide(self._scroll.viewport())
        self._scroll.horizontalScrollBar().valueChanged.connect(
            self._area.set_scroll_x
        )
        # ビューポートの高さ変化(ウィンドウのリサイズや横スクロールバーの出入り)を
        # 拾って、行が少なくても下端まで描かせる
        self._scroll.viewport().installEventFilter(self)
        self._area.seek_requested.connect(self.seek_requested)
        self._area.intervals_edited.connect(self.intervals_edited)
        self._area.delete_requested.connect(self.delete_requested)
        self._area.selection_changed.connect(self.selection_changed)
        self._area.scroll_requested.connect(self._scroll_to)
        self._area.hscroll_requested.connect(self._scroll_by)
        self._area.playback_toggle_requested.connect(self.playback_toggle_requested)
        self._area.step_requested.connect(self.step_requested)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt のオーバーライド)
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._area.set_viewport_height(event.size().height())
            self._guide.reposition()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt のオーバーライド)
        """開いた直後からキー操作が効くよう、本体へフォーカスを渡す"""
        super().showEvent(event)
        self._area.setFocus()
        self._guide.reposition()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt のオーバーライド)
        self.closed.emit()
        super().closeEvent(event)

    def _scroll_to(self, x: int) -> None:
        """ズームのアンカー補正(カーソル下のフレームを画面上に留める)"""
        self._scroll.horizontalScrollBar().setValue(x)

    def _scroll_by(self, delta: int) -> None:
        """ホイールによる横スクロール(現在位置からの相対移動)"""
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.value() + delta)

    def set_total(self, total: int) -> None:
        self._area.set_total(total)

    def set_data(self, regions: list[VideoRegion]) -> None:
        self._area.set_data(regions)
        # 区間が並ぶとバーに重なって邪魔になるため、空のときだけ案内を出す
        self._guide.setVisible(not regions)
        self._guide.reposition()

    def set_selection(self, regions: list[Region]) -> None:
        self._area.set_selection(regions)

    def set_frame(self, frame: int) -> None:
        """再生ヘッドを移動し、可視範囲から外れたらスクロールで追従する"""
        self._area.set_frame(frame)
        x = self._area.frame_x(frame)
        bar = self._scroll.horizontalScrollBar()
        view_w = self._scroll.viewport().width()
        if not (bar.value() + LABEL_W <= x <= bar.value() + view_w):
            bar.setValue(int(x - view_w * 0.2))
