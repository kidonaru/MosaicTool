"""モザイク処理: ブロック平均モザイクの生成とマスク合成(GUI 非依存)"""
from __future__ import annotations

from math import ceil

from PIL import Image, ImageDraw
from PySide6.QtGui import QPainterPath


def cell_counts(size: tuple[int, int], block: int) -> tuple[int, int]:
    """モザイクのマス数を返す(make_mosaic_image と同じ分割)"""
    w, h = size
    return max(1, (w + block - 1) // block), max(1, (h + block - 1) // block)


def cell_edges(length: int, cells: int) -> list[int]:
    """マス境界の画素座標を返す(要素数は cells + 1)

    Image.Resampling.NEAREST での拡大は画素中心を基準に元画素へ写すため、
    境界は ceil(i * length / cells - 0.5) になる。マスは端数なく均等割りされる。
    """
    return [ceil(i * length / cells - 0.5) for i in range(cells + 1)]


def make_mosaic_image(img: Image.Image, block: int) -> Image.Image:
    """画像全体をブロック平均でモザイク化する(縮小→ニアレスト拡大)"""
    if block <= 1:
        return img.copy()
    small = img.resize(cell_counts(img.size, block), Image.Resampling.BOX)
    return small.resize(img.size, Image.Resampling.NEAREST)


def paths_to_mask(paths: list[QPainterPath], size: tuple[int, int]) -> Image.Image:
    """QPainterPath 群を白黒マスク("L")に変換する"""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for path in paths:
        for poly in path.toFillPolygons():
            pts = [(pt.x(), pt.y()) for pt in poly]
            if len(pts) >= 3:
                draw.polygon(pts, fill=255)
    return mask


def mask_to_cell_grid(mask: Image.Image, block: int, threshold: float) -> Image.Image:
    """マスクをマス(セル)解像度の二値画像に落とす

    セルごとのマスク被覆率が threshold (0.0〜1.0) 以上なら 255。
    被覆率 0 のセルは threshold が 0 でも 0 のまま。
    """
    cells_w, cells_h = cell_counts(mask.size, block)
    # BOX 縮小はセルの実面積で平均するため、画素値がそのまま「被覆率 x 255」になる
    coverage = mask.resize((cells_w, cells_h), Image.Resampling.BOX)
    limit = max(1, round(threshold * 255))
    return coverage.point(lambda v: 255 if v >= limit else 0)


def snap_mask_to_grid(mask: Image.Image, block: int, threshold: float) -> Image.Image:
    """マスクをモザイクのマス目に合わせて二値化する

    セル内の被覆率が threshold 以上ならセル全体を塗る。
    これにより範囲の境目でもモザイクのマスの形が保たれる。
    """
    if block <= 1:
        return mask
    grid = mask_to_cell_grid(mask, block, threshold)
    # make_mosaic_image と同じ拡大をするため、マス目が完全に一致する
    return grid.resize(mask.size, Image.Resampling.NEAREST)


def cell_grid_to_rects(
    grid: Image.Image, size: tuple[int, int]
) -> list[tuple[int, int, int, int]]:
    """セルグリッドの塗るセルを画像座標の矩形リストに変換する

    grid は mask_to_cell_grid が返す "L" モード画像を前提とする(1 セル 1 バイトとして読む)。
    行ごとに連続するセルを 1 本の矩形へまとめる。矩形同士は互いに素なので、
    QPainterPath へ addRect で積んでも偶奇規則で打ち消し合わない。
    """
    cells_w, cells_h = grid.size
    xs = cell_edges(size[0], cells_w)
    ys = cell_edges(size[1], cells_h)
    data = grid.tobytes()
    rects: list[tuple[int, int, int, int]] = []
    for cy in range(cells_h):
        row = data[cy * cells_w : (cy + 1) * cells_w]
        start = None
        for cx in range(cells_w + 1):
            on = cx < cells_w and row[cx] != 0
            if on and start is None:
                start = cx
            elif not on and start is not None:
                rects.append(
                    (xs[start], ys[cy], xs[cx] - xs[start], ys[cy + 1] - ys[cy])
                )
                start = None
    return rects


def apply_mosaic(
    img: Image.Image, paths: list[QPainterPath], block: int, threshold: float = 0.0
) -> Image.Image:
    """範囲パス内にのみモザイクをかけた画像を返す(元画像は変更しない)

    モザイクはマス単位で適用され、セル内の被覆率が threshold 以上のセルだけが塗られる。
    """
    if not paths:
        return img.copy()
    mosaic = make_mosaic_image(img, block)
    mask = snap_mask_to_grid(paths_to_mask(paths, img.size), block, threshold)
    out = img.copy()
    out.paste(mosaic, (0, 0), mask)
    return out
