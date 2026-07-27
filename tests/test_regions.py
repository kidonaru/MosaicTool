from PySide6.QtCore import QPointF, QRectF

from mosaic_tool.regions import Region, RegionKind


def test_rect_local_path_bounds():
    r = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 100, 50))
    assert r.local_path().boundingRect() == QRectF(0, 0, 100, 50)


def test_translation():
    r = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 100, 50), pos=QPointF(10, 20))
    assert r.image_path().boundingRect() == QRectF(10, 20, 100, 50)


def test_rotation_90_around_center():
    # 中心 (50,25) 回りに 90 度回転 → 幅と高さが入れ替わり中心は不変
    r = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 100, 50), rotation=90)
    br = r.image_path().boundingRect()
    assert abs(br.width() - 50) < 1e-6
    assert abs(br.height() - 100) < 1e-6
    assert abs(br.center().x() - 50) < 1e-6
    assert abs(br.center().y() - 25) < 1e-6


def test_scale_around_center():
    r = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 100, 50), scale_x=2.0, scale_y=2.0)
    br = r.image_path().boundingRect()
    assert abs(br.width() - 200) < 1e-6
    assert abs(br.height() - 100) < 1e-6
    assert abs(br.center().x() - 50) < 1e-6
    assert abs(br.center().y() - 25) < 1e-6


def test_non_uniform_scale_around_center():
    # 横だけ 2 倍 → 幅のみ倍増し中心は不変
    r = Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 100, 50), scale_x=2.0)
    br = r.image_path().boundingRect()
    assert abs(br.width() - 200) < 1e-6
    assert abs(br.height() - 50) < 1e-6
    assert abs(br.center().x() - 50) < 1e-6
    assert abs(br.center().y() - 25) < 1e-6


def test_stroke_path_covers_line():
    # 太さ 20 の水平線 (丸キャップ) → 高さ約 20、幅約 120
    r = Region(kind=RegionKind.STROKE, points=[QPointF(0, 0), QPointF(100, 0)], pen_width=20)
    br = r.local_path().boundingRect()
    assert abs(br.height() - 20) < 1.0
    assert abs(br.width() - 120) < 1.0
