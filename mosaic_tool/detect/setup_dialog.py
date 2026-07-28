"""推論環境のセットアップ用ダイアログ(GPU/CPU の選択と進捗表示)"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
)

from mosaic_tool.detect.runtime import RuntimeInstaller, has_nvidia_gpu

INTRO = (
    "自動検出を使うには、推論用の実行環境を用意する必要があります。\n"
    "ダウンロードには時間がかかります(回線状況により数分〜十数分)。"
)
GPU_LABEL = "GPU を使う (NVIDIA / ダウンロード 約 2.5GB / 検出が速い)"
CPU_LABEL = "CPU のみ (ダウンロード 約 250MB / どの環境でも動く)"


class RuntimeSetupDialog(QDialog):
    """セットアップの選択と実行。完了すると accept() する"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自動検出のセットアップ")
        self.resize(680, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(INTRO))
        self._gpu_radio = QRadioButton(GPU_LABEL)
        self._cpu_radio = QRadioButton(CPU_LABEL)
        # NVIDIA GPU がありそうなら GPU 版を既定にする
        self._gpu_radio.setChecked(has_nvidia_gpu())
        self._cpu_radio.setChecked(not self._gpu_radio.isChecked())
        layout.addWidget(self._gpu_radio)
        layout.addWidget(self._cpu_radio)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始")
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self._cancel)
        layout.addWidget(self._buttons)

        self._installer = RuntimeInstaller(self)
        self._installer.progress.connect(self._log.appendPlainText)
        self._installer.finished.connect(self._on_finished)
        self._running = False

    def _start(self) -> None:
        self._running = True
        self._set_inputs_enabled(False)
        self._installer.start(use_gpu=self._gpu_radio.isChecked())

    def _cancel(self) -> None:
        if self._running:
            self._installer.cancel()
            return
        self.reject()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._gpu_radio.setEnabled(enabled)
        self._cpu_radio.setEnabled(enabled)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _on_finished(self, ok: bool, message: str) -> None:
        self._running = False
        self._log.appendPlainText(message)
        if ok:
            self.accept()
            return
        self._set_inputs_enabled(True)
