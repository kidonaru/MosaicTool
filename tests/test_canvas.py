"""RegionItem の変形(辺/角ハンドルによるリサイズ)と MosaicCanvas のクリップ更新の検証"""
import os

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QMouseEvent,
    QNativeGestureEvent,
    QPointingDevice,
    QWheelEvent,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from PIL import Image  # noqa: E402

from mosaic_tool.canvas import MosaicCanvas, RegionItem, ToolMode  # noqa: E402
from mosaic_tool.mosaic import paths_to_mask, snap_mask_to_grid  # noqa: E402
from mosaic_tool.regions import Region, RegionKind  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def resize(
    handle: str,
    scene_pt: QPointF,
    grab_pt: QPointF | None = None,
    rotation: float = 0.0,
    steps: int = 1,
) -> QRectF:
    """100x50 の矩形範囲のハンドルを掴んで scene_pt まで動かした結果を返す"""
    item = RegionItem(
        Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 100, 50), rotation=rotation)
    )
    item.setSelected(True)
    grab = grab_pt if grab_pt is not None else item.mapToScene(item._handle_points()[handle])
    item.begin_resize(handle, grab)
    # steps > 1: ドラッグ途中の状態を経由しても最終形が同じ(累積誤差なし)ことの確認用
    for i in range(1, steps + 1):
        item._resize_to(grab + (scene_pt - grab) * (i / steps), Qt.KeyboardModifier.NoModifier)
    item.sync_model()
    return item.region.image_path().boundingRect()


def test_right_edge_resize_keeps_left_edge(qapp):
    # 右辺だけを伸ばす → 幅のみ変化し、左辺と高さは動かない
    assert resize("r", QPointF(200, 25)) == QRectF(0, 0, 200, 50)


def test_bottom_edge_resize_keeps_top_edge(qapp):
    assert resize("b", QPointF(50, 150)) == QRectF(0, 0, 100, 150)


def test_corner_resize_scales_both_axes(qapp):
    # 角は縦横を独立に変形でき、対角の角が固定される
    assert resize("br", QPointF(300, 150)) == QRectF(0, 0, 300, 150)


def test_resize_is_unaffected_by_intermediate_steps(qapp):
    # 途中経過を挟んでも最終形は同じ(ドラッグ中に変形量がずれない)
    assert resize("r", QPointF(200, 25), steps=10) == QRectF(0, 0, 200, 50)


def test_rotated_resize_keeps_rectangle(qapp):
    # 90 度回転した範囲を右辺ハンドルで伸ばす → 歪まず長方形のまま伸びる
    # ハンドルはシーン上 (50, 75) にあり、+y 方向へ 100 引くと局所 x が 2 倍になる
    assert resize("r", QPointF(50, 175), rotation=90.0) == QRectF(25, -25, 50, 200)


def test_resize_follows_cursor_delta_when_grabbed_off_center(qapp):
    # ハンドル中心から 5px ずれた位置で掴んでも、変形量はカーソルの移動量と一致する
    grab = QPointF(105, 30)  # 右辺ハンドル(100, 25)から (+5, +5) ずれた位置
    assert resize("r", grab, grab) == QRectF(0, 0, 100, 50)
    assert resize("r", grab + QPointF(50, 0), grab) == QRectF(0, 0, 150, 50)


def test_overlay_clip_is_snapped_to_cells(qapp):
    """プレビューのクリップ形状が保存時のマス単位マスクと一致する"""
    canvas = MosaicCanvas()
    canvas.set_block_size(10)
    canvas.set_threshold(0.5)
    canvas.set_image(
        Image.new("RGB", (100, 100)),
        [Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 27, 100))],
    )
    clip = canvas._overlay._clip
    # 3 マス目(20〜30)は 80% 覆われるためマス全体が含まれ、4 マス目は含まれない
    assert clip.contains(QPointF(29, 50))
    assert not clip.contains(QPointF(31, 50))
    # 保存時のマスクと同じ判定になっている
    mask = snap_mask_to_grid(paths_to_mask(canvas.image_paths(), (100, 100)), 10, 0.5)
    for x in (5, 25, 29, 31, 95):
        assert clip.contains(QPointF(x, 50)) == (mask.getpixel((x, 50)) == 255)


def _canvas_with_image(qapp) -> MosaicCanvas:
    canvas = MosaicCanvas()
    canvas.set_image(Image.new("RGB", (200, 200), (0, 0, 0)))
    return canvas


def _rect_region(x: float) -> Region:
    return Region(kind=RegionKind.RECT, rect=QRectF(x, 0, 10, 10))


def test_add_regions_adds_all(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_regions([_rect_region(0), _rect_region(20)])
    assert len(canvas.get_regions()) == 2


def test_add_regions_undo_removes_all_at_once(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_regions([_rect_region(0), _rect_region(20)])
    canvas.undo()
    assert canvas.get_regions() == []


def test_add_regions_keeps_existing_regions(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_region(_rect_region(100))
    canvas.add_regions([_rect_region(0), _rect_region(20)])
    canvas.undo()
    # 手で引いた範囲は残る
    assert len(canvas.get_regions()) == 1


def test_add_regions_leaves_items_unselected(qapp):
    # 自動検出の追加分は非選択。意図せず全体を動かしてしまう事故を防ぐ
    canvas = _canvas_with_image(qapp)
    old = canvas.add_region(_rect_region(100))
    old.setSelected(True)
    items = canvas.add_regions([_rect_region(0), _rect_region(20)])
    assert items and all(not item.isSelected() for item in items)
    assert not old.isSelected()


def test_add_region_keeps_selection_for_manual_drawing(qapp):
    # 手描き直後の選択はそのまま(描いてすぐ変形できるようにする)
    canvas = _canvas_with_image(qapp)
    item = canvas.add_region(_rect_region(0))
    item.setSelected(True)
    assert item.isSelected()


def test_add_regions_with_empty_list_pushes_no_undo(qapp):
    canvas = _canvas_with_image(qapp)
    canvas.add_region(_rect_region(100))
    canvas.add_regions([])
    canvas.undo()
    # 空追加は Undo を消費しないため、直前の追加が取り消される
    assert canvas.get_regions() == []


def _press(canvas: MosaicCanvas, scene_pt: QPointF) -> None:
    """シーン座標を指定して左ボタンの押下を送る"""
    pos = QPointF(canvas.mapFromScene(scene_pt))
    canvas.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pos,
            pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


CREATION_MODES = [(ToolMode.RECT, "_rect_start"), (ToolMode.PEN, "_pen_points")]


@pytest.mark.parametrize("mode, attr", CREATION_MODES)
def test_press_inside_image_starts_creation(qapp, mode, attr):
    canvas = _canvas_with_image(qapp)
    canvas.set_mode(mode)
    _press(canvas, QPointF(100, 100))
    assert getattr(canvas, attr)


@pytest.mark.parametrize("mode, attr", CREATION_MODES)
def test_press_outside_image_does_not_start_creation(qapp, mode, attr):
    # 画像の外側(余白)を押しても範囲は作らない
    canvas = _canvas_with_image(qapp)
    canvas.set_mode(mode)
    _press(canvas, QPointF(-30, -30))
    assert not getattr(canvas, attr)


def _wheel(pixel_delta: QPoint, angle_delta: QPoint, modifiers=Qt.KeyboardModifier.NoModifier):
    return QWheelEvent(
        QPointF(50, 50),
        QPointF(50, 50),
        pixel_delta,
        angle_delta,
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _zoomed_canvas(qapp) -> MosaicCanvas:
    """スクロールバーが動く状態(画像がビューポートより大きい)のキャンバス"""
    canvas = _canvas_with_image(qapp)
    canvas.resize(100, 100)
    canvas._zoom_at(4.0, QPointF(50, 50))
    return canvas


def test_mouse_wheel_zooms(qapp):
    # ピクセル delta を持たない通常のホイールは従来どおりズーム
    canvas = _zoomed_canvas(qapp)
    before = canvas.transform().m11()
    canvas.wheelEvent(_wheel(QPoint(0, 0), QPoint(0, 120)))
    assert canvas.transform().m11() > before


def test_trackpad_scroll_slides_image_without_zoom(qapp):
    # トラックパッドの 2 本指スクロールは拡縮せず画像をスライドさせる
    canvas = _zoomed_canvas(qapp)
    before = canvas.transform().m11()
    vbar = canvas.verticalScrollBar()
    start = vbar.value()
    canvas.wheelEvent(_wheel(QPoint(0, -30), QPoint(0, -120)))
    assert canvas.transform().m11() == before
    assert vbar.value() != start


def test_trackpad_scroll_with_modifier_zooms(qapp):
    # 修飾キー併用時はトラックパッドでもズーム(ピンチが使えない環境の代替)
    canvas = _zoomed_canvas(qapp)
    before = canvas.transform().m11()
    canvas.wheelEvent(
        _wheel(QPoint(0, 30), QPoint(0, 120), Qt.KeyboardModifier.ControlModifier)
    )
    assert canvas.transform().m11() > before


def _pinch(canvas: MosaicCanvas, value: float) -> None:
    ev = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        QPointingDevice(),
        2,
        QPointF(50, 50),
        QPointF(50, 50),
        QPointF(50, 50),
        value,
        QPointF(0, 0),
    )
    canvas.viewportEvent(ev)


def test_pinch_out_zooms_in(qapp):
    canvas = _zoomed_canvas(qapp)
    before = canvas.transform().m11()
    _pinch(canvas, 0.2)
    assert canvas.transform().m11() > before


def test_pinch_in_zooms_out(qapp):
    canvas = _zoomed_canvas(qapp)
    before = canvas.transform().m11()
    _pinch(canvas, -0.2)
    assert canvas.transform().m11() < before
