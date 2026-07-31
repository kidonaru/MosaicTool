"""動画モードのタイムライン UI(シーク・検出間隔)

区間の表示と編集はタイムラインウィンドウ(video/timeline_window.py)が担う。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

# 検出間隔の上限 (フレーム)。これを超える間引きは漏れが大きく実用にならない
DETECT_STEP_MAX = 120


class TimelineBar(QWidget):
    """キャンバス下に出すタイムライン。動画モードのときだけ表示する"""

    frame_changed = Signal(int)  # シークやコマ送りでフレームが変わった

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
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(lambda: self.step(1))
        layout.addWidget(self._next_btn)
        self._frame_label = QLabel(" 0 / 0 ")
        layout.addWidget(self._frame_label)
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

    def seek(self, frame: int) -> None:
        """外部(タイムラインウィンドウ)からのシーク。frame_changed を発火する"""
        self._slider.setValue(frame)

    def step(self, delta: int) -> None:
        self._slider.setValue(self._slider.value() + delta)

    def detect_step(self) -> int:
        return self._step_spin.value()

    def _update_label(self) -> None:
        self._frame_label.setText(
            f" {self._slider.value()} / {self._slider.maximum()} "
        )
