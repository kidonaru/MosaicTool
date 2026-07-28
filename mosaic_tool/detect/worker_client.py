"""常駐する検出ワーカーの制御(起動・リクエスト・応答の切り出し)"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from mosaic_tool.detect import paths
from mosaic_tool.detect.convert import DetectError, build_request, parse_response

# 1 枚あたりの検出を待つ上限(モデル読み込みを含む初回を見込んで長めに取る)
DETECT_TIMEOUT_MS = 120_000
# 異常終了時に表示する stderr の末尾の文字数
STDERR_TAIL = 500


def worker_command(python: Path, script: Path, models: list[Path]) -> list[str]:
    """ワーカーの起動コマンド(モデルは引数として並べて渡す)"""
    return [str(python), str(script), *(str(m) for m in models)]


def install_worker_script() -> Path:
    """ワーカー本体を runtime/ へコピーする

    venv の Python はパッケージ内のモジュールを解決できないため、
    実体のスクリプトファイルとして置く必要がある。内容は毎回上書きし、
    アプリを更新したときに古いワーカーが残らないようにする。
    """
    destination = paths.worker_script_installed()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(paths.worker_script_source(), destination)
    return destination


class DetectWorker(QObject):
    """検出ワーカーとの通信を受け持つ

    モデル読み込みに数秒かかるためプロセスは常駐させ、異常終了しても
    次のリクエストで黙って起動し直す。
    """

    detected = Signal(list)              # 検出結果 (list[dict])
    progress = Signal(int, int, str)     # (完了数, 総数, モデル名)
    failed = Signal(str)                 # エラーメッセージ

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._buffer = ""
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DETECT_TIMEOUT_MS)
        self._timer.timeout.connect(self._on_timeout)

    def is_busy(self) -> bool:
        return self._busy

    def request(self, image_path: str, models: dict, device: str) -> None:
        """検出を依頼する(結果は detected / failed で返る)

        models はファイル名をキー、信頼度(0〜1)を値とする。
        ワーカーは models\\ を全件読み込むが、推論するのはここに載せたものだけ。
        """
        if self._busy:
            return
        if not models:
            self.failed.emit("有効な検出モデルがありません")
            return
        available = paths.model_files()
        if not available:
            self.failed.emit(f"検出モデルが見つかりません: {paths.models_dir()}")
            return
        if self._process is None and not self._start(available):
            return
        self._busy = True
        self._timer.start()
        self._process.write(build_request(image_path, models, device).encode("utf-8"))

    def stop(self) -> None:
        """ワーカーを終了する(アプリ終了時に呼ぶ)"""
        self._timer.stop()
        process, self._process = self._process, None
        self._busy = False
        self._buffer = ""
        if process is None:
            return
        process.terminate()
        if not process.waitForFinished(3000):
            process.kill()

    def _start(self, models: list[Path]) -> bool:
        try:
            script = install_worker_script()
        except OSError as e:
            self.failed.emit(f"ワーカーの設置に失敗しました: {e}")
            return False
        cmd = worker_command(paths.venv_python(), script, models)
        process = QProcess(self)
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.finished.connect(self._on_process_finished)
        process.errorOccurred.connect(self._on_process_error)
        process.start(cmd[0], cmd[1:])
        if not process.waitForStarted(10_000):
            self.failed.emit(f"検出ワーカーを起動できませんでした: {cmd[0]}")
            return False
        self._process = process
        self._buffer = ""
        return True

    def _on_stdout(self) -> None:
        if self._process is None:
            return
        self._feed(
            bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        )

    def _feed(self, chunk: str) -> None:
        """標準出力の断片を受け取り、行が揃うたびに 1 応答として処理する"""
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        try:
            response = parse_response(line)
        except DetectError as e:
            self._finish_request()
            self.failed.emit(str(e))
            return
        if response.ready:
            # 起動直後のモデル読み込み完了通知。待っている呼び出し元は無い
            return
        if response.progress is not None:
            self.progress.emit(*response.progress)
            return
        self._finish_request()
        self.detected.emit(response.detections or [])

    def _finish_request(self) -> None:
        self._busy = False
        self._timer.stop()

    def _on_timeout(self) -> None:
        self._finish_request()
        self.stop()
        self.failed.emit("検出がタイムアウトしました")

    def _on_process_finished(self, exit_code: int, _status) -> None:
        # 応答待ちのまま終了したときだけエラーとして扱う(次回は起動し直す)
        was_busy = self._busy
        detail = ""
        if self._process is not None:
            detail = bytes(self._process.readAllStandardError()).decode(
                "utf-8", errors="replace"
            )[-STDERR_TAIL:]
        self._process = None
        self._finish_request()
        if was_busy:
            self.failed.emit(
                f"検出ワーカーが終了しました (終了コード {exit_code})\n{detail}".strip()
            )

    def _on_process_error(self, _error) -> None:
        if self._busy:
            self._finish_request()
            self.failed.emit("検出ワーカーとの通信に失敗しました")
