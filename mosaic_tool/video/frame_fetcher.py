"""シーク用のフレーム取り出しを GUI スレッドの外で行う

シークのたびに ffmpeg を同期実行すると 1 回あたり 100ms 超 GUI が固まり、
ルーラーのドラッグ中はイベントごとに積み上がって操作不能になる。
取り出しは専用スレッドで行い、取り出し中に届いた要求は最新の 1 件だけ残す
(途中の要求は取り出さず、完了済みでも古くなった画像は表示に流さない)。
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from mosaic_tool.video import ffmpeg as video_ffmpeg
from mosaic_tool.video.ffmpeg import VideoInfo
# 停止時の待ち上限は再生スレッドと同じ値を使う (片方だけずれるのを防ぐ)。
# 取り出し中のプロセスは kill するため通常は即座に終わる
from mosaic_tool.video.player import STOP_WAIT_MS


class FrameFetcher(QThread):
    """要求されたフレームを PNG バイト列として順に取り出すスレッド"""

    frame_ready = Signal(int, object)  # (フレーム番号, PNG バイト列)
    failed = Signal(int, str)

    def __init__(self, path: Path, info: VideoInfo, parent=None):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._cond = threading.Condition()
        self._request: int | None = None
        self._quit = False
        self._proc: subprocess.Popen | None = None

    def request(self, frame: int) -> None:
        """frame の取り出しを頼む(未処理の要求があれば置き換える)"""
        with self._cond:
            self._request = frame
            self._cond.notify()

    def stop(self) -> None:
        """スレッドを終わらせる(取り出し中ならプロセスを切って待たない)"""
        with self._cond:
            self._quit = True
            proc = self._proc
            self._cond.notify()
        video_ffmpeg.kill_process(proc)
        self.wait(STOP_WAIT_MS)

    def run(self) -> None:
        while True:
            with self._cond:
                while self._request is None and not self._quit:
                    self._cond.wait()
                if self._quit:
                    return
                frame = self._request
                self._request = None
            try:
                data = self._extract(frame)
            except video_ffmpeg.VideoError as e:
                # 停止による kill もエラーとして返るため、通知せず終了へ向かう
                if self._quit:
                    return
                self.failed.emit(frame, str(e))
                continue
            with self._cond:
                # 取り出している間に新しい要求が来ていたら、古い画像は流さない
                if self._request is not None or self._quit:
                    continue
            self.frame_ready.emit(frame, data)

    def _extract(self, frame: int) -> bytes:
        """extract_frame と同等の取り出し。stop() から kill できるようプロセスを持つ"""
        cmd = video_ffmpeg.extract_frame_command(self._path, frame, self._info)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=video_ffmpeg.subprocess_flags(),
            )
        except OSError as e:
            raise video_ffmpeg.VideoError(f"ffmpeg を実行できません: {e}") from e
        with self._cond:
            self._proc = proc
            # stop() が _proc を見た後に起動された場合はここで切る
            if self._quit:
                proc.kill()
        try:
            out, err = proc.communicate(timeout=video_ffmpeg.EXTRACT_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            proc.communicate()
            raise video_ffmpeg.VideoError(f"ffmpeg を実行できません: {e}") from e
        finally:
            with self._cond:
                self._proc = None
        if proc.returncode != 0 or not out:
            detail = err.decode("utf-8", errors="replace").strip()
            raise video_ffmpeg.VideoError(
                f"フレームを取り出せません (frame {frame})\n{detail}"
            )
        return out
