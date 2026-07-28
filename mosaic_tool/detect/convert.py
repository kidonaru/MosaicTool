"""検出結果 JSON と Region の相互変換(GUI・推論ライブラリ非依存)"""
from __future__ import annotations

import json
import math

from PySide6.QtCore import QPointF, QRectF

from mosaic_tool.regions import Region, RegionKind

# 輪郭点の間引き距離。画像の対角長に対する比率で決める
# (画素数に依らず、見た目の粗さが揃うようにするため)
POLYGON_SIMPLIFY_RATIO = 0.004
# 多角形として成立する最小の点数
MIN_POLYGON_POINTS = 3


class DetectError(Exception):
    """ワーカーからのエラー応答、または応答を解釈できなかったことを表す"""


def build_request(image_path: str, conf: float, device: str) -> str:
    """ワーカーへ送るリクエスト 1 行(改行付き)を組み立てる"""
    payload = {"image": image_path, "conf": conf, "device": device}
    return json.dumps(payload, ensure_ascii=False) + "\n"


def parse_response(line: str) -> list[dict]:
    """ワーカーの応答 1 行を解釈して検出リストを返す"""
    try:
        payload = json.loads(line)
    except (ValueError, TypeError) as e:
        raise DetectError(f"検出結果を解釈できませんでした: {line[:200]}") from e
    if not isinstance(payload, dict):
        raise DetectError(f"検出結果の形式が不正です: {line[:200]}")
    if not payload.get("ok"):
        raise DetectError(payload.get("error") or "検出に失敗しました")
    return payload.get("detections", [])


def thin_points(points: list[QPointF], min_distance: float) -> list[QPointF]:
    """隣接点の距離が min_distance 未満の点を落とす

    輪郭は数百点で返ることがあり、そのまま持つとハンドル操作のたびの
    パス再構築が重くなるため間引く。3 点未満になる場合は元の点列を返す。
    """
    if not points:
        return []
    kept = [points[0]]
    for pt in points[1:]:
        if math.hypot(pt.x() - kept[-1].x(), pt.y() - kept[-1].y()) >= min_distance:
            kept.append(pt)
    if len(kept) < MIN_POLYGON_POINTS:
        return list(points)
    return kept


def _polygon_region(polygon: list, min_distance: float) -> Region | None:
    """輪郭点列から POLYGON 範囲を作る。点が足りなければ None"""
    points = [QPointF(float(x), float(y)) for x, y in polygon]
    if len(points) < MIN_POLYGON_POINTS:
        return None
    return Region(kind=RegionKind.POLYGON, points=thin_points(points, min_distance))


def _rect_region(bbox: list) -> Region | None:
    """bbox から RECT 範囲を作る。値が足りなければ None"""
    if not bbox or len(bbox) < 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    return Region(kind=RegionKind.RECT, rect=QRectF(x1, y1, x2 - x1, y2 - y1))


def detections_to_regions(
    detections: list[dict], image_size: tuple[int, int]
) -> list[Region]:
    """検出結果を範囲へ変換する

    セグメンテーションの輪郭があれば多角形、無ければ bbox の矩形にする。
    どちらも取れない検出は読み飛ばす。
    """
    width, height = image_size
    min_distance = math.hypot(width, height) * POLYGON_SIMPLIFY_RATIO
    regions: list[Region] = []
    for det in detections:
        region = _polygon_region(det.get("polygon") or [], min_distance)
        if region is None:
            region = _rect_region(det.get("bbox") or [])
        if region is not None:
            regions.append(region)
    return regions
