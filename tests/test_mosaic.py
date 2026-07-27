from PIL import Image
from PySide6.QtGui import QPainterPath

from mosaic_tool.mosaic import (
    apply_mosaic,
    cell_grid_to_rects,
    make_mosaic_image,
    mask_to_cell_grid,
    paths_to_mask,
    snap_mask_to_grid,
)


def _rect_mask(size: tuple[int, int], box: tuple[int, int, int, int]) -> Image.Image:
    """box の矩形だけが 255 のマスクを作る"""
    path = QPainterPath()
    path.addRect(*box)
    return paths_to_mask([path], size)


def _gradient_image() -> Image.Image:
    """左上から右下へ色が変わる 100x100 のテスト画像"""
    img = Image.new("RGB", (100, 100))
    px = img.load()
    for y in range(100):
        for x in range(100):
            px[x, y] = (x * 2, y * 2, 0)
    return img


def test_make_mosaic_uniform_blocks():
    m = make_mosaic_image(_gradient_image(), 10)
    assert m.size == (100, 100)
    # 同一ブロック内は同色、隣のブロックとは別色
    assert m.getpixel((0, 0)) == m.getpixel((9, 9))
    assert m.getpixel((0, 0)) != m.getpixel((10, 0))


def test_paths_to_mask():
    path = QPainterPath()
    path.addRect(0, 0, 50, 100)
    mask = paths_to_mask([path], (100, 100))
    assert mask.getpixel((25, 50)) == 255  # 範囲内
    assert mask.getpixel((75, 50)) == 0    # 範囲外


def test_apply_mosaic_only_inside_mask():
    img = _gradient_image()
    path = QPainterPath()
    path.addRect(0, 0, 50, 100)
    out = apply_mosaic(img, [path], 10)
    # 範囲外は変化しない
    assert out.getpixel((80, 80)) == img.getpixel((80, 80))
    # 範囲内はブロック内が同色になる
    assert out.getpixel((0, 0)) == out.getpixel((9, 9))


def test_apply_mosaic_empty_paths_returns_copy():
    img = _gradient_image()
    out = apply_mosaic(img, [], 10)
    assert out.tobytes() == img.tobytes()
    assert out is not img


def test_snap_rounds_partial_cells_at_half():
    # 幅 27px の矩形: 3 マス目(20〜30)の被覆率は 70%、4 マス目(30〜40)は 0%
    mask = _rect_mask((100, 100), (0, 0, 27, 100))
    snapped = snap_mask_to_grid(mask, 10, 0.5)
    assert snapped.getpixel((29, 50)) == 255  # 70% >= 50% なのでマス全体が塗られる
    assert snapped.getpixel((31, 50)) == 0

    # 幅 23px なら 3 マス目の被覆率は 30% で、しきい値未満のため塗られない
    snapped = snap_mask_to_grid(_rect_mask((100, 100), (0, 0, 23, 100)), 10, 0.5)
    assert snapped.getpixel((19, 50)) == 255
    assert snapped.getpixel((21, 50)) == 0


def test_snap_threshold_zero_paints_any_touched_cell():
    mask = _rect_mask((100, 100), (0, 0, 21, 100))
    snapped = snap_mask_to_grid(mask, 10, 0.0)
    assert snapped.getpixel((29, 50)) == 255  # 1px でも触れていれば塗る
    assert snapped.getpixel((31, 50)) == 0    # 触れていないマスは塗らない


def test_snap_threshold_full_paints_only_covered_cells():
    # addRect は端点を含むため 0〜28 の 29px が塗られる → 3 マス目の被覆率は 90%
    mask = _rect_mask((100, 100), (0, 0, 28, 100))
    snapped = snap_mask_to_grid(mask, 10, 1.0)
    assert snapped.getpixel((19, 50)) == 255  # 完全に覆われたマス
    assert snapped.getpixel((21, 50)) == 0    # 90% しか覆われていないマス


def test_snap_grid_matches_mosaic_grid_for_odd_sizes():
    """画像サイズが block の倍数でなくてもマス目が make_mosaic_image と一致する"""
    img = Image.new("RGB", (105, 63))
    px = img.load()
    for y in range(63):
        for x in range(105):
            px[x, y] = (x * 2, y * 4, 0)
    mosaic = make_mosaic_image(img, 10)
    path = QPainterPath()
    path.addRect(0, 0, 105, 63)
    out = apply_mosaic(img, [path], 10, 0.5)
    # 全面を覆えばモザイク画像そのものになる(グリッドがズレていれば一致しない)
    assert out.tobytes() == mosaic.tobytes()


def test_snap_noop_for_block_one():
    mask = _rect_mask((100, 100), (0, 0, 27, 100))
    assert snap_mask_to_grid(mask, 1, 0.5) is mask


def test_apply_mosaic_boundary_is_cell_aligned():
    img = _gradient_image()
    path = QPainterPath()
    path.addRect(0, 0, 27, 100)
    out = apply_mosaic(img, [path], 10, 0.5)
    # 3 マス目(20〜30)は途中で切れずマス全体が置き換わる
    assert out.getpixel((29, 50)) != img.getpixel((29, 50))
    assert out.getpixel((29, 50)) == out.getpixel((21, 50))
    # 4 マス目は手つかず
    assert out.getpixel((31, 50)) == img.getpixel((31, 50))


def test_cell_grid_to_rects_merges_runs():
    grid = mask_to_cell_grid(_rect_mask((100, 100), (0, 0, 29, 19)), 10, 0.5)
    # 2 行 x 3 マスが 1 行 1 矩形にまとまる
    assert cell_grid_to_rects(grid, (100, 100)) == [(0, 0, 30, 10), (0, 10, 30, 10)]


def test_cell_grid_to_rects_covers_image_for_odd_sizes():
    """マス目が均等割りでも矩形が隙間なく画像全体を覆う"""
    size = (105, 63)
    grid = mask_to_cell_grid(_rect_mask(size, (0, 0, 105, 63)), 10, 0.5)
    rects = cell_grid_to_rects(grid, size)
    assert sum(w * h for _, _, w, h in rects) == size[0] * size[1]
    assert max(x + w for x, _, w, _ in rects) == size[0]
    assert max(y + h for _, y, _, h in rects) == size[1]
