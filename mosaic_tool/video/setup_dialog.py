"""動画対応のセットアップ: ffmpeg / ffprobe のダウンロードと配置

推論ランタイムと同じく同梱はせず、初回に静的ビルドの zip を取得して
runtime/ffmpeg/ へ展開する。ダウンロードには既存の ModelDownloader を使う。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from mosaic_tool.detect.downloader import ModelDownloader
from mosaic_tool.video import ffmpeg

INTRO = (
    "動画を扱うには、変換用の実行環境 (ffmpeg) を用意する必要があります。\n"
    "初回のみ静的ビルド (Windows 約 110MB / macOS 約 50MB) をダウンロードします。"
)


@dataclass(frozen=True)
class _Download:
    """取得する zip 1 件と、そこから取り出す実行ファイル名

    実行ファイルをそのまま起動する経路のため、配布元の差し替え・改ざんに
    気づかず実行しないよう、バージョン固定 URL と SHA-256 で内容を検証する。
    """

    url: str
    binaries: tuple[str, ...]
    sha256: str


def planned_downloads() -> tuple[_Download, ...]:
    """OS ごとの取得計画

    Windows は gyan.dev の essentials ビルド(ffmpeg / ffprobe 同梱)、
    macOS は evermeet.cx の公式ビルド(実行ファイルごとに zip が分かれる。
    Intel バイナリだが Apple Silicon でも Rosetta で動く)。
    """
    if sys.platform == "win32":
        return (
            _Download(
                "https://www.gyan.dev/ffmpeg/builds/packages/"
                "ffmpeg-8.1.2-essentials_build.zip",
                ("ffmpeg.exe", "ffprobe.exe"),
                "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
            ),
        )
    return (
        _Download(
            "https://evermeet.cx/ffmpeg/ffmpeg-8.1.2.zip",
            ("ffmpeg",),
            "e91df72a1ee7c26606f90dd2dd4dcccc6a75140ff9ea6fdd50faae828b82ba69",
        ),
        _Download(
            "https://evermeet.cx/ffmpeg/ffprobe-8.1.2.zip",
            ("ffprobe",),
            "399b93f0b9862f69767afa343e90c2f48d7e7958cadbb6deb76a012d0e3b7ce3",
        ),
    )


def install_from_zip(zip_path: Path, binaries: tuple[str, ...]) -> None:
    """zip から実行ファイルを探して runtime/ffmpeg/ へ配置する

    ビルドごとにフォルダ構成が異なるため、名前一致でツリー全体から探す。
    見つからなければ VideoError。
    """
    dest_dir = ffmpeg.ffmpeg_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    remaining = set(binaries)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = Path(member.filename).name
            if member.is_dir() or name not in remaining:
                continue
            dest = dest_dir / name
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            dest.chmod(0o755)
            remaining.discard(name)
    if remaining:
        raise ffmpeg.VideoError(
            f"アーカイブに実行ファイルが見つかりません: {', '.join(sorted(remaining))}"
        )


class VideoSetupDialog(QDialog):
    """ffmpeg のダウンロードと配置。完了すると accept() する"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("動画対応のセットアップ")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(INTRO))
        self._status = QLabel("")
        layout.addWidget(self._status)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        layout.addWidget(self._bar)
        self._start_btn = QPushButton("セットアップ")
        self._start_btn.clicked.connect(self._start)
        layout.addWidget(self._start_btn)
        self._cancel_btn = QPushButton("キャンセル")
        self._cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self._cancel_btn)

        self._downloader = ModelDownloader(self)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished.connect(self._on_downloaded)
        self._queue: list[_Download] = []
        self._current: _Download | None = None
        self._tmp_dir: Path | None = None
        self._dest: Path | None = None

    def _start(self) -> None:
        self._start_btn.setEnabled(False)
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="mosaic_ffmpeg_"))
        self._queue = list(planned_downloads())
        self._next_download()

    def _next_download(self) -> None:
        if not self._queue:
            self._finish(True, "")
            return
        self._current = self._queue.pop(0)
        self._status.setText("ダウンロード中...")
        self._bar.setValue(0)
        dest = self._tmp_dir / f"download_{len(self._queue)}.zip"
        self._dest = dest
        self._downloader.start(self._current.url, dest, sha256=self._current.sha256)

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self._bar.setValue(int(received * 100 / total))

    def _on_downloaded(self, ok: bool, message: str) -> None:
        if not ok:
            self._finish(False, message)
            return
        self._status.setText("展開中...")
        try:
            install_from_zip(self._dest, self._current.binaries)
        except (OSError, zipfile.BadZipFile, ffmpeg.VideoError) as e:
            self._finish(False, f"展開に失敗しました: {e}")
            return
        self._next_download()

    def _finish(self, ok: bool, message: str) -> None:
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        if ok:
            self.accept()
            return
        QMessageBox.critical(self, "セットアップエラー", message)
        self._start_btn.setEnabled(True)
        self._status.setText("")
        self._bar.setValue(0)

    def reject(self) -> None:
        self._downloader.cancel()
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        super().reject()
