"""フレーム番号と時刻表記の変換(動画モードの各 UI で共用する)"""
from __future__ import annotations


def format_timecode(frame: int, fps: float) -> str:
    """フレーム番号を MM:SS.ss (1 時間以上は H:MM:SS.ss) の表記にする"""
    seconds = frame / fps if fps > 0 else 0.0
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours >= 1:
        return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"
    return f"{int(minutes):02d}:{secs:05.2f}"
