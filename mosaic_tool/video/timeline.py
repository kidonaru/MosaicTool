"""動画モードのタイムライン UI(シーク・再生操作)

区間の表示と編集はタイムラインウィンドウ(video/timeline_window.py)が担い、
検出の条件は検出範囲ダイアログ(video/detect_range_dialog.py)が持つ。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

PLAY_TEXT = "▶"
PAUSE_TEXT = "⏸"

# 再生速度の選択肢(倍率)。既定は 1.0
SPEEDS = (0.25, 0.5, 1.0, 2.0)
DEFAULT_SPEED = 1.0


class TimelineBar(QWidget):
    """キャンバス下に出すタイムライン。動画モードのときだけ表示する"""

    frame_changed = Signal(int)   # シークやコマ送りでフレームが変わった
    play_clicked = Signal()       # ▶ / ⏸ が押された(実際の開始・停止は app 側)
    speed_changed = Signal(float)  # 再生速度が変わった

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.clicked.connect(lambda: self.step(-1))
        layout.addWidget(self._prev_btn)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self.frame_changed)
        self._slider.valueChanged.connect(lambda _: self._update_label())
        layout.addWidget(self._slider, 1)
        self._next_btn = QPushButton("▶|")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(lambda: self.step(1))
        layout.addWidget(self._next_btn)
        self._frame_label = QLabel(" 0 / 0 ")
        layout.addWidget(self._frame_label)
        # 再生ボタンは Space のショートカットと二重に効かないようフォーカスを持たせない
        self._play_btn = QPushButton(PLAY_TEXT)
        self._play_btn.setFixedWidth(28)
        self._play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_btn.clicked.connect(self.play_clicked)
        layout.addWidget(self._play_btn)
        layout.addWidget(QLabel(" 速度 "))
        self._speed_combo = QComboBox()
        for speed in SPEEDS:
            self._speed_combo.addItem(f"{speed}x", speed)
        self._speed_combo.setCurrentIndex(SPEEDS.index(DEFAULT_SPEED))
        self._speed_combo.currentIndexChanged.connect(
            lambda _: self.speed_changed.emit(self.speed())
        )
        layout.addWidget(self._speed_combo)

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

    def seek(self, frame: int) -> None:
        """外部(タイムラインウィンドウ)からのシーク。frame_changed を発火する"""
        self._slider.setValue(frame)

    def step(self, delta: int) -> None:
        self._slider.setValue(self._slider.value() + delta)

    def set_playing(self, playing: bool) -> None:
        """再生中かどうかをボタンの表示へ反映する"""
        self._play_btn.setText(PAUSE_TEXT if playing else PLAY_TEXT)

    def speed(self) -> float:
        return float(self._speed_combo.currentData())

    def _update_label(self) -> None:
        self._frame_label.setText(
            f" {self._slider.value()} / {self._slider.maximum()} "
        )
