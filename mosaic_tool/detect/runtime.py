"""推論環境(venv)のセットアップ: uv で Python と ultralytics/torch を用意する"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from mosaic_tool.detect import paths

# venv に入れる Python のバージョン(ultralytics/torch の対応が安定している系列)
PYTHON_VERSION = "3.11"
PACKAGES = ["ultralytics", "torch", "torchvision"]
# CUDA 版 torch の配布元(automosaic と同じ cu121 系)
TORCH_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu121"


def venv_command(uv: Path, runtime: Path) -> list[str]:
    """runtime/ に venv を作るコマンド"""
    return [str(uv), "venv", str(runtime), "--python", PYTHON_VERSION]


def install_command(uv: Path, runtime: Path, use_gpu: bool) -> list[str]:
    """runtime/ の venv へ推論パッケージを入れるコマンド

    GPU 版は torch の配布元が PyPI ではないため、追加のインデックスを指定する。
    """
    cmd = [str(uv), "pip", "install", "--python", str(runtime), *PACKAGES]
    if use_gpu:
        cmd += ["--extra-index-url", TORCH_CUDA_INDEX_URL]
    return cmd


def has_nvidia_gpu() -> bool:
    """NVIDIA GPU がありそうか(セットアップ時の既定値の出し分けに使う)"""
    return shutil.which("nvidia-smi") is not None


class RuntimeInstaller(QObject):
    """venv 作成 → パッケージ導入 を順に実行する(非同期)"""

    progress = Signal(str)          # 進捗ログ 1 行
    finished = Signal(bool, str)    # (成功したか, メッセージ)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._steps: list[list[str]] = []
        self._cancelled = False

    def start(self, use_gpu: bool) -> None:
        uv = paths.bundled_uv_path()
        if not uv.is_file():
            self.finished.emit(False, f"uv が見つかりません: {uv}")
            return
        runtime_dir = paths.runtime_dir()
        self._cancelled = False
        self._steps = [
            venv_command(uv, runtime_dir),
            install_command(uv, runtime_dir, use_gpu),
        ]
        self._run_next()

    def cancel(self) -> None:
        self._cancelled = True
        self._steps = []
        if self._process is not None:
            self._process.kill()

    def _run_next(self) -> None:
        if not self._steps:
            self.finished.emit(True, "推論環境のセットアップが完了しました")
            return
        cmd = self._steps.pop(0)
        self.progress.emit(f"> {' '.join(cmd)}")
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_step_finished)
        self._process = process
        process.start(cmd[0], cmd[1:])

    def _on_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        for line in text.splitlines():
            if line.strip():
                self.progress.emit(line)

    def _on_step_finished(self, exit_code: int, _status) -> None:
        self._process = None
        if self._cancelled:
            self._cleanup()
            self.finished.emit(False, "セットアップを中止しました")
            return
        if exit_code != 0:
            self._cleanup()
            self.finished.emit(False, f"セットアップに失敗しました (終了コード {exit_code})")
            return
        self._run_next()

    def _cleanup(self) -> None:
        """中途半端な venv を残さない(次回はやり直しから始められる)"""
        shutil.rmtree(paths.runtime_dir(), ignore_errors=True)
