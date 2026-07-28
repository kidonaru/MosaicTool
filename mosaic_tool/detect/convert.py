"""検出結果 JSON と Region の相互変換(GUI・推論ライブラリ非依存)"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF

from mosaic_tool.regions import Region, RegionKind

# 輪郭点の間引き距離。画像の対角長に対する比率で決める
# (画素数に依らず、見た目の粗さが揃うようにするため)
POLYGON_SIMPLIFY_RATIO = 0.004
# 多角形として成立する最小の点数
MIN_POLYGON_POINTS = 3


class DetectError(Exception):
    """ワーカーからのエラー応答、または応答を解釈できなかったことを表す"""


@dataclass(frozen=True)
class WorkerResponse:
    """ワーカーの応答 1 行の中身(4 種のうちどれか 1 つだけが埋まる)"""

    ready: bool = False
    progress: tuple[int, int, str] | None = None   # (完了数, 総数, モデル名)
    detections: list[dict] | None = None
    classes: dict[str, list[str]] | None = None    # {ファイル名: クラス名の列}


def build_request(image_path: str, models: dict[str, dict], device: str) -> str:
    """ワーカーへ送る検出リクエスト 1 行(改行付き)を組み立てる

    models はファイル名をキー、{"conf": 信頼度(0〜1), "classes": クラス名} を値とする。
    ここに載っていないモデルはワーカー側で推論されない。
    classes が空なら、そのモデルの全クラスが対象になる。
    """
    payload = {
        "command": "detect",
        "image": image_path,
        "models": models,
        "device": device,
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


def build_classes_request() -> str:
    """読み込み済みモデルのクラス一覧を問い合わせる 1 行(改行付き)"""
    return json.dumps({"command": "classes"}, ensure_ascii=False) + "\n"


def _classes_payload(value: dict) -> dict[str, list[str]]:
    """クラス一覧の応答を {ファイル名: list[str]} へ揃える"""
    return {
        str(name): [str(c) for c in names] if isinstance(names, list) else []
        for name, names in value.items()
    }


def parse_response(line: str) -> WorkerResponse:
    """ワーカーの応答 1 行を解釈する(失敗は DetectError)"""
    try:
        payload = json.loads(line)
    except (ValueError, TypeError) as e:
        raise DetectError(f"検出結果を解釈できませんでした: {line[:200]}") from e
    if not isinstance(payload, dict):
        raise DetectError(f"検出結果の形式が不正です: {line[:200]}")
    if not payload.get("ok"):
        raise DetectError(payload.get("error") or "検出に失敗しました")
    if payload.get("ready"):
        return WorkerResponse(ready=True)
    progress = payload.get("progress")
    if isinstance(progress, dict):
        return WorkerResponse(
            progress=(
                int(progress.get("done", 0)),
                int(progress.get("total", 0)),
                str(progress.get("model", "")),
            )
        )
    classes = payload.get("classes")
    if isinstance(classes, dict):
        return WorkerResponse(classes=_classes_payload(classes))
    return WorkerResponse(detections=payload.get("detections", []))


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
