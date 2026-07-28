"""標準モデルのダウンロード(QtNetwork を使い、本体に依存を増やさない)"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from mosaic_tool.detect import paths
from mosaic_tool.detect.catalog import MODELS, CatalogModel

PART_SUFFIX = ".part"
MAX_ATTEMPTS = 3       # 初回を含む試行回数
RETRY_DELAY_MS = 2000  # 再試行までの待ち時間(回線の一時的な不調を跨ぐため)
HASH_CHUNK_SIZE = 1 << 20
CANCELLED_TEXT = "ダウンロードを中止しました"


def pending_models() -> list[CatalogModel]:
    """models\\ にまだ置かれていない標準モデル"""
    directory = paths.models_dir()
    return [m for m in MODELS if not (directory / m.filename).is_file()]


def part_path(destination: Path) -> Path:
    """書き込み中の一時ファイル名

    中断したファイルが .pt として一覧に現れ、壊れたモデルとして
    読み込みに失敗するのを防ぐ。
    """
    return destination.with_name(destination.name + PART_SUFFIX)


def file_sha256(path: Path) -> str:
    """ファイルの SHA-256(数 MB あるので分割して読む)"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


class ModelDownloader(QObject):
    """1 ファイルのダウンロード(非同期)

    通信エラーは一時的なことが多いため、MAX_ATTEMPTS まで自動で再試行する。
    """

    progress = Signal(int, int)      # (受信バイト, 全体バイト。不明なら 0)
    retrying = Signal(str)           # 再試行の通知(ログ表示用)
    finished = Signal(bool, str)     # (成功したか, メッセージ)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._file = None
        self._url = ""
        self._destination: Path | None = None
        self._sha256 = ""
        self._attempt = 0
        self._cancelled = False

    def start(self, url: str, destination: Path, sha256: str = "") -> None:
        """url を destination へ保存する(結果は finished で返る)

        sha256 を渡すと、保存前に内容が一致することを確かめる。
        """
        self._cancelled = False
        self._url = url
        self._destination = destination
        self._sha256 = sha256
        self._attempt = 0
        self._request()

    def _request(self) -> None:
        """1 回分の取得を開始する"""
        self._attempt += 1
        url = self._url
        destination = self._destination
        part = part_path(destination)
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
            self._file = part.open("wb")
        except OSError as e:
            # 保存先の問題は再試行しても直らないので即座に打ち切る
            self._emit_finished(False, f"保存先を開けません: {part}\n{e}")
            return
        request = QNetworkRequest(QUrl(url))
        # HuggingFace は CDN へリダイレクトするため追従が要る
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self._manager.get(request)
        reply.readyRead.connect(self._on_ready_read)
        reply.downloadProgress.connect(self.progress.emit)
        reply.finished.connect(self._on_finished)
        self._reply = reply

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        if self._reply is not None:
            self._reply.abort()
            return
        # 再試行の待機中は abort する対象がないので、ここで打ち切りを伝える
        if self._destination is not None:
            self._discard()
            self.finished.emit(False, CANCELLED_TEXT)

    def _on_ready_read(self) -> None:
        if self._reply is not None and self._file is not None:
            self._file.write(bytes(self._reply.readAll()))

    def _close_file(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _discard(self) -> None:
        """書きかけを残さない"""
        self._close_file()
        if self._destination is not None:
            part_path(self._destination).unlink(missing_ok=True)

    def _emit_finished(self, ok: bool, message: str) -> None:
        """1 件分の決着。以後の cancel() が再び反応しないよう状態を空にする"""
        self._destination = None
        self.finished.emit(ok, message)

    def _schedule_retry(self, message: str) -> None:
        self._discard()
        self.retrying.emit(
            f"{message} — 再試行します ({self._attempt + 1}/{MAX_ATTEMPTS})"
        )
        QTimer.singleShot(RETRY_DELAY_MS, self._on_retry_timeout)

    def _on_retry_timeout(self) -> None:
        # 待機中に中止・完了していたら何もしない
        if self._cancelled or self._destination is None:
            return
        self._request()

    def _verify(self, part: Path, name: str) -> bool:
        """受信した内容がカタログのハッシュと一致するか確かめる

        壊れた受信なら再試行で直るため、通信エラーと同じ扱いにする。
        繰り返し合わない場合は配布元が差し替わった可能性があり、
        pickle を含む .pt をそのまま読み込ませないよう失敗にする。
        """
        if not self._sha256:
            return True
        try:
            actual = file_sha256(part)
        except OSError as e:
            self._discard()
            self._emit_finished(False, f"ファイルを確認できません: {part}\n{e}")
            return False
        if actual == self._sha256:
            return True
        message = f"ファイルの検証に失敗しました: {name}"
        if self._attempt < MAX_ATTEMPTS:
            self._schedule_retry(message)
            return False
        self._discard()
        self._emit_finished(False, message)
        return False

    def _on_finished(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        error = reply.error()
        message = reply.errorString()
        reply.deleteLater()
        if self._cancelled:
            self._discard()
            self._emit_finished(False, CANCELLED_TEXT)
            return
        if error != QNetworkReply.NetworkError.NoError:
            if self._attempt < MAX_ATTEMPTS:
                self._schedule_retry(f"ダウンロードに失敗しました: {message}")
                return
            self._discard()
            self._emit_finished(False, f"ダウンロードに失敗しました: {message}")
            return
        self._close_file()
        destination = self._destination
        part = part_path(destination)
        if not self._verify(part, destination.name):
            return
        try:
            part.replace(destination)
        except OSError as e:
            self._discard()
            self._emit_finished(False, f"保存に失敗しました: {destination}\n{e}")
            return
        self._emit_finished(True, f"取得しました: {destination.name}")
