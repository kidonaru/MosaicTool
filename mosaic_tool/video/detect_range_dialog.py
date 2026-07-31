"""動画の自動検出の適用範囲(開始・終了フレーム)と検出間隔を決めるダイアログ"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from mosaic_tool.video.timecode import format_timecode

# 検出間隔の上限 (フレーム)。これを超える間引きは漏れが大きく実用にならない
DETECT_STEP_MAX = 120

STEP_TOOLTIP = "自動検出を何フレームおきに行うか。増やすと速くなるが漏れやすくなる"


def detect_frame_count(start: int, end: int, step: int) -> int:
    """範囲内で実際に検出するフレーム数(両端含み、step 間引き)"""
    if end < start:
        return 0
    return (end - start) // max(1, step) + 1


class DetectRangeDialog(QDialog):
    """検出の適用範囲を指定するモーダルダイアログ

    件数の表示が実行前の確認を兼ねる(従来の確認メッセージは出さない)。
    """

    def __init__(
        self,
        total_frames: int,
        fps: float,
        step: int,
        parent=None,
    ):
        super().__init__(parent)
        self._fps = fps
        self.setWindowTitle("検出範囲")
        last = max(0, total_frames - 1)
        # 既定は動画全体。表示中フレームからではなく必ず先頭から始める
        self._start = QSpinBox()
        self._start.setRange(0, last)
        self._start.setValue(0)
        self._end = QSpinBox()
        self._end.setRange(0, last)
        self._end.setValue(last)
        self._step = QSpinBox()
        self._step.setRange(1, DETECT_STEP_MAX)
        self._step.setValue(step)
        self._step.setSuffix(" フレーム")
        self._step.setToolTip(STEP_TOOLTIP)
        self._start_time = QLabel()
        self._end_time = QLabel()
        self._count_label = QLabel()

        grid = QGridLayout()
        rows = (
            ("開始フレーム", self._start, self._start_time),
            ("終了フレーム", self._end, self._end_time),
        )
        for row, (text, spin, time_label) in enumerate(rows):
            grid.addWidget(QLabel(text), row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(time_label, row, 2)
        grid.addWidget(QLabel("検出間隔"), len(rows), 0)
        grid.addWidget(self._step, len(rows), 1)
        # 右端に余白を持たせ、ラベルが間延びしないようにする
        grid.setColumnStretch(3, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(self._count_label)
        layout.addWidget(buttons)

        for spin in (self._start, self._end, self._step):
            spin.valueChanged.connect(self._on_value_changed)
        self._on_value_changed()

    def _on_value_changed(self) -> None:
        """開始 > 終了 にならないよう互いの範囲を狭め、表示を作り直す"""
        self._end.setMinimum(self._start.value())
        self._start.setMaximum(self._end.value())
        self._start_time.setText(format_timecode(self._start.value(), self._fps))
        self._end_time.setText(format_timecode(self._end.value(), self._fps))
        count = detect_frame_count(
            self._start.value(), self._end.value(), self._step.value()
        )
        self._count_label.setText(f"約 {count} フレームを検出します")

    def range_result(self) -> tuple[int, int, int]:
        """(開始フレーム, 終了フレーム, 検出間隔)"""
        return self._start.value(), self._end.value(), self._step.value()
