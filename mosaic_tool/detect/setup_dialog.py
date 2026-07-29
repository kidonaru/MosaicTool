"""推論環境のセットアップ用ダイアログ(GPU/CPU の選択、進捗表示、標準モデルの取得)"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QVBoxLayout,
)

from mosaic_tool.detect import downloader, paths, runtime
from mosaic_tool.detect.catalog import CatalogModel
from mosaic_tool.detect.downloader import ModelDownloader
from mosaic_tool.detect.runtime import RuntimeInstaller, has_nvidia_gpu

INTRO = (
    "自動検出を使うには、推論用の実行環境を用意する必要があります。\n"
    "ダウンロードには時間がかかります(回線状況により数分〜十数分)。\n"
    "続けて標準の検出モデル(顔・目 / 合計 約 13MB)を取得します。"
)
GPU_LABEL = "GPU を使う (NVIDIA / ダウンロード 約 2.5GB / 検出が速い)"
GPU_DETECTED_NOTE = " ※NVIDIA GPU を検出しました"
CPU_LABEL = "CPU のみ (ダウンロード 約 250MB / どの環境でも動く)"


class RuntimeSetupDialog(QDialog):
    """セットアップの選択と実行。完了すると accept() する

    venv を構築したあと、まだ置かれていない標準モデルを順に取得する。
    モデルの取得に失敗しても venv があれば手動でモデルを置いて使えるため、
    セットアップ自体は成功として扱う。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自動検出のセットアップ")
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(INTRO))
        # macOS はインストール内容が 1 通りしかないため選択肢を出さない
        self._gpu_radio: QRadioButton | None = None
        self._cpu_radio: QRadioButton | None = None
        if runtime.supports_gpu_choice():
            gpu_label = GPU_LABEL + (GPU_DETECTED_NOTE if has_nvidia_gpu() else "")
            self._gpu_radio = QRadioButton(gpu_label)
            self._cpu_radio = QRadioButton(CPU_LABEL)
            # 既定は常に CPU。GPU は容量が大きいため明示的に選んでもらう
            self._cpu_radio.setChecked(True)
            layout.addWidget(self._gpu_radio)
            layout.addWidget(self._cpu_radio)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)
        self._bar = QProgressBar()
        self._bar.setVisible(False)
        layout.addWidget(self._bar)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始")
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self._cancel)
        layout.addWidget(self._buttons)

        self._installer = RuntimeInstaller(self)
        self._installer.progress.connect(self._log.appendPlainText)
        self._installer.finished.connect(self._on_runtime_finished)
        self._downloader = ModelDownloader(self)
        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.retrying.connect(self._on_download_retrying)
        self._downloader.finished.connect(self._on_download_finished)
        self._queue: list[CatalogModel] = []
        self._total = 0
        self._running = False

    def _start(self) -> None:
        self._running = True
        self._set_inputs_enabled(False)
        use_gpu = self._gpu_radio is not None and self._gpu_radio.isChecked()
        self._installer.start(use_gpu=use_gpu)

    def _cancel(self) -> None:
        if self._running:
            self._installer.cancel()
            self._downloader.cancel()
            return
        self.reject()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for radio in (self._gpu_radio, self._cpu_radio):
            if radio is not None:
                radio.setEnabled(enabled)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _on_runtime_finished(self, ok: bool, message: str) -> None:
        self._log.appendPlainText(message)
        if not ok:
            self._running = False
            self._bar.setVisible(False)
            self._set_inputs_enabled(True)
            return
        self._queue = list(downloader.pending_models())
        self._total = len(self._queue)
        self._start_next_download()

    def _start_next_download(self) -> None:
        if not self._queue:
            self._running = False
            self._bar.setVisible(False)
            self.accept()
            return
        model = self._queue[0]
        done = self._total - len(self._queue) + 1
        self._log.appendPlainText(
            f"モデルを取得中: {model.filename} ({done}/{self._total})"
        )
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)  # 全体サイズが分かるまでは不確定表示
        paths.models_dir().mkdir(parents=True, exist_ok=True)
        self._downloader.start(
            model.url, paths.models_dir() / model.filename, model.sha256
        )

    def _on_download_progress(self, received: int, total: int) -> None:
        if total <= 0:
            return
        self._bar.setRange(0, total)
        self._bar.setValue(received)

    def _on_download_retrying(self, message: str) -> None:
        self._log.appendPlainText(message)
        self._bar.setRange(0, 0)  # 受信量が振り出しに戻るため不確定表示へ

    def _on_download_finished(self, ok: bool, message: str) -> None:
        self._log.appendPlainText(message)
        if self._queue:
            self._queue.pop(0)
        # 取得に失敗しても続行する(次回のセットアップで再試行される)
        self._start_next_download()
