"""動画モードのタイムライン UI(シーク・区間バー・検出間隔)"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# 検出間隔の上限 (フレーム)。これを超える間引きは漏れが大きく実用にならない
DETECT_STEP_MAX = 120

# 区間バーの高さと、端ハンドルのつかみ判定幅 (px)
STRIP_HEIGHT = 14
HANDLE_PX = 6

# 帯の色。選択中だけ濃くして操作対象を示す
_BAND_COLOR = QColor(100, 150, 240, 90)
_SELECTED_COLOR = QColor(60, 120, 255, 220)
_HANDLE_COLOR = QColor(255, 255, 255, 230)


class IntervalStrip(QWidget):
    """全範囲の適用区間を帯で表示し、選択中の区間を端ドラッグで調整するバー"""

    interval_edited = Signal(int, int)  # 選択中の区間の (開始, 終了) が変わった
    interval_clicked = Signal(int)      # 薄い帯がクリックされた (regions の index)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(STRIP_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._intervals: list[tuple[int, int]] = []
        self._selected: int | None = None
        self._total = 0
        self._drag: str | None = None  # None | "start" | "end"

    def set_data(
        self, intervals: list[tuple[int, int]], selected: int | None, total: int
    ) -> None:
        """表示する区間一覧・選択 index・総フレーム数を更新する"""
        self._intervals = list(intervals)
        self._selected = selected
        self._total = total
        self.update()

    # --- フレーム↔X 座標 ---

    def _x(self, frame: int) -> float:
        if self._total <= 1:
            return 0.0
        return frame / (self._total - 1) * (self.width() - 1)

    def _frame_at(self, x: float) -> int:
        if self._total <= 1 or self.width() <= 1:
            return 0
        frame = round(x / (self.width() - 1) * (self._total - 1))
        return max(0, min(self._total - 1, frame))

    # --- 描画 ---

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        for i, (start, end) in enumerate(self._intervals):
            if i == self._selected:
                continue
            self._draw_band(painter, start, end, _BAND_COLOR, margin=3)
        if self._selected is not None and self._selected < len(self._intervals):
            start, end = self._intervals[self._selected]
            self._draw_band(painter, start, end, _SELECTED_COLOR, margin=1)
            # 端ハンドル(白い縦線)でドラッグできることを示す
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_HANDLE_COLOR)
            for frame in (start, end):
                x = self._x(frame)
                painter.drawRect(int(x) - 1, 1, 3, self.height() - 2)
        painter.end()

    def _draw_band(
        self, painter: QPainter, start: int, end: int, color: QColor, margin: int
    ) -> None:
        x1, x2 = self._x(start), self._x(end)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        # 1 フレームの区間でも見えるよう最低幅を確保する
        width = max(3.0, x2 - x1)
        painter.drawRect(int(x1), margin, int(width) + 1, self.height() - margin * 2)

    # --- マウス操作 ---

    def _hit_edge(self, x: float) -> str | None:
        """選択中の帯の端 (±HANDLE_PX) に掛かっていればどちらの端かを返す"""
        if self._selected is None or self._selected >= len(self._intervals):
            return None
        start, end = self._intervals[self._selected]
        d_start = abs(x - self._x(start))
        d_end = abs(x - self._x(end))
        if min(d_start, d_end) > HANDLE_PX:
            return None
        return "start" if d_start <= d_end else "end"

    def _hit_band(self, x: float) -> int | None:
        """クリック位置に掛かる帯の index を返す(後に描いたものを優先)"""
        for i in reversed(range(len(self._intervals))):
            start, end = self._intervals[i]
            if self._x(start) - HANDLE_PX <= x <= self._x(end) + HANDLE_PX:
                return i
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        self._drag = self._hit_edge(x)
        if self._drag is not None:
            return
        index = self._hit_band(x)
        if index is not None:
            self.interval_clicked.emit(index)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None or self._selected is None:
            return
        start, end = self._intervals[self._selected]
        frame = self._frame_at(event.position().x())
        # 反対側の端を越えないようクランプする
        if self._drag == "start":
            start = min(frame, end)
        else:
            end = max(frame, start)
        if (start, end) != self._intervals[self._selected]:
            self._intervals[self._selected] = (start, end)
            self.update()
            self.interval_edited.emit(start, end)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None


class TimelineBar(QWidget):
    """キャンバス下に出すタイムライン。動画モードのときだけ表示する"""

    frame_changed = Signal(int)         # シークやコマ送りでフレームが変わった
    interval_edited = Signal(int, int)  # 選択中の範囲の区間がドラッグで変わった
    interval_clicked = Signal(int)      # 区間バーの帯がクリックされた

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.clicked.connect(lambda: self.step(-1))
        layout.addWidget(self._prev_btn)
        # 区間バーとスライダーを縦に並べ、同じ幅で対応づける
        self._strip = IntervalStrip()
        self._strip.interval_edited.connect(self._on_interval_edited)
        self._strip.interval_clicked.connect(self.interval_clicked)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self.frame_changed)
        self._slider.valueChanged.connect(lambda _: self._update_label())
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(1)
        center.addWidget(self._strip)
        center.addWidget(self._slider)
        layout.addLayout(center, 1)
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(lambda: self.step(1))
        layout.addWidget(self._next_btn)
        self._frame_label = QLabel(" 0 / 0 ")
        layout.addWidget(self._frame_label)
        # 選択中の範囲の適用区間。区間バーの端ドラッグで変更できる
        self._interval_label = QLabel("区間: -")
        self._interval_label.setToolTip(
            "選択中の範囲の適用区間。上のバーの両端をドラッグして変更できる"
        )
        layout.addWidget(self._interval_label)
        # 自動検出の間引き間隔。1 なら全フレームを検出する
        layout.addWidget(QLabel(" 検出間隔 "))
        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, DETECT_STEP_MAX)
        self._step_spin.setValue(1)
        self._step_spin.setSuffix(" フレーム")
        self._step_spin.setToolTip(
            "自動検出を何フレームおきに行うか。増やすと速くなるが漏れやすくなる"
        )
        layout.addWidget(self._step_spin)

    def set_range(self, total_frames: int) -> None:
        self._slider.setRange(0, max(0, total_frames - 1))
        self._update_label()

    def set_frame(self, frame: int) -> None:
        """表示位置を合わせる(frame_changed は発火させない)"""
        self._slider.blockSignals(True)
        self._slider.setValue(frame)
        self._slider.blockSignals(False)
        self._update_label()

    def frame(self) -> int:
        return self._slider.value()

    def step(self, delta: int) -> None:
        self._slider.setValue(self._slider.value() + delta)

    def detect_step(self) -> int:
        return self._step_spin.value()

    def set_intervals(
        self, intervals: list[tuple[int, int]], selected: int | None
    ) -> None:
        """区間バーの表示と選択中の区間ラベルを更新する"""
        self._strip.set_data(intervals, selected, self._slider.maximum() + 1)
        self._update_interval_label(intervals, selected)

    def _on_interval_edited(self, start: int, end: int) -> None:
        self._update_interval_label([(start, end)], 0)
        self.interval_edited.emit(start, end)

    def _update_interval_label(
        self, intervals: list[tuple[int, int]], selected: int | None
    ) -> None:
        if selected is not None and selected < len(intervals):
            start, end = intervals[selected]
            self._interval_label.setText(f"区間: {start} 〜 {end}")
        else:
            self._interval_label.setText("区間: -")

    def _update_label(self) -> None:
        self._frame_label.setText(
            f" {self._slider.value()} / {self._slider.maximum()} "
        )
