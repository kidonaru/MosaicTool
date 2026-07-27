from pathlib import Path

from PIL import Image

from icon_assets import ICON_SIZES, build_icon_assets, ico_sizes


def test_build_icon_assets_creates_all_ico_sizes(tmp_path: Path):
    source = tmp_path / "master.png"
    Image.new("RGBA", (512, 512), (10, 40, 44, 255)).save(source, "PNG")
    ico_output = tmp_path / "icon.ico"

    build_icon_assets(source, ico_output)

    assert ico_sizes(ico_output) == {(size, size) for size in ICON_SIZES}


def test_build_icon_assets_rejects_non_square_png(tmp_path: Path):
    source = tmp_path / "master.png"
    Image.new("RGBA", (512, 256), (10, 40, 44, 255)).save(source, "PNG")

    try:
        build_icon_assets(source, tmp_path / "icon.ico")
    except ValueError as exc:
        assert str(exc) == "PNGマスターは正方形である必要があります"
    else:
        raise AssertionError("非正方形のPNGが受理されました")
