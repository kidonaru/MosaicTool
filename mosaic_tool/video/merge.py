"""検出結果を区間範囲へ変換する(GUI・Qt 非依存)

検出フレームごとに独立して扱い、検出 1 件をそのまま
[検出フレーム, 次の検出フレームの直前] の区間範囲にする。
別フレームの検出結果は追跡・合成せず、互いに影響しない。
"""
from __future__ import annotations

from dataclasses import dataclass

Box = tuple[float, float, float, float]  # (x1, y1, x2, y2)
Polygon = tuple[tuple[float, float], ...]  # セグメンテーションの輪郭点列


@dataclass(frozen=True)
class Detection:
    """検出 1 件。polygon はセグメンテーションモデルの輪郭(無ければ None)"""

    frame: int
    bbox: Box
    polygon: Polygon | None = None


@dataclass(frozen=True)
class Interval:
    """区間範囲 1 個。polygon が無ければ bbox の矩形として扱わせる"""

    start: int
    end: int
    bbox: Box
    polygon: Polygon | None = None


def merge_detections(
    detections: list[Detection],
    *,
    step: int = 1,
    total_frames: int,
) -> list[Interval]:
    """検出を区間範囲にする

    detections: Detection のリスト。フレーム番号は step の倍数
    step: 検出間隔。次の検出フレームの直前まで対象が居るとみなす
    total_frames: 動画の総フレーム数(区間末尾のクランプ用)
    """
    intervals: list[Interval] = []
    for det in detections:
        end = min(total_frames - 1, det.frame + step - 1)
        intervals.append(Interval(det.frame, end, det.bbox, det.polygon))
    return intervals


def parse_detection(detection: dict, frame: int) -> Detection | None:
    """ワーカーの検出 1 件を Detection にする。bbox も輪郭も無ければ None"""
    polygon: Polygon | None = None
    raw = detection.get("polygon") or []
    if len(raw) >= 3:
        polygon = tuple((float(x), float(y)) for x, y in raw)
    bbox = detection.get("bbox") or []
    if len(bbox) >= 4:
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        return Detection(frame, (x1, y1, x2, y2), polygon)
    if polygon is not None:
        xs = [x for x, _ in polygon]
        ys = [y for _, y in polygon]
        return Detection(frame, (min(xs), min(ys), max(xs), max(ys)), polygon)
    return None
