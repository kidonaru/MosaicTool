from pathlib import Path

from PIL import Image

from icon_assets import (
    ICNS_REQUIRED_SIZES,
    ICON_SIZES,
    build_icon_assets,
    icns_sizes,
    ico_sizes,
)


def _master(tmp_path: Path, size: tuple[int, int] = (512, 512)) -> Path:
    source = tmp_path / "master.png"
    Image.new("RGBA", size, (10, 40, 44, 255)).save(source, "PNG")
    return source


def test_build_icon_assets_creates_all_ico_sizes(tmp_path: Path):
    ico_output = tmp_path / "icon.ico"

    build_icon_assets(_master(tmp_path), ico_output, tmp_path / "icon.icns")

    assert ico_sizes(ico_output) == {(size, size) for size in ICON_SIZES}


def test_build_icon_assets_creates_icns_with_required_sizes(tmp_path: Path):
    icns_output = tmp_path / "icon.icns"

    build_icon_assets(_master(tmp_path), tmp_path / "icon.ico", icns_output)

    assert icns_output.read_bytes()[:4] == b"icns"
    assert ICNS_REQUIRED_SIZES <= icns_sizes(icns_output)


def test_build_icon_assets_rejects_non_square_png(tmp_path: Path):
    source = _master(tmp_path, (512, 256))

    try:
        build_icon_assets(source, tmp_path / "icon.ico", tmp_path / "icon.icns")
    except ValueError as exc:
        assert str(exc) == "PNGマスターは正方形である必要があります"
    else:
        raise AssertionError("非正方形のPNGが受理されました")


def test_repository_icns_is_up_to_date():
    """コミット済みの assets/icon.icns が macOS ビルドで使える形式であること"""
    assets = Path(__file__).resolve().parent.parent / "assets"
    icns = assets / "icon.icns"
    assert icns.is_file()
    assert ICNS_REQUIRED_SIZES <= icns_sizes(icns)
