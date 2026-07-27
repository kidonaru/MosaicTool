"""MosaicToolの配布用アイコン資産を生成する。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_icon_assets(source: Path, ico_output: Path) -> None:
    with Image.open(source) as image:
        if image.width != image.height:
            raise ValueError("PNGマスターは正方形である必要があります")
        master = image.convert("RGBA")

    master.save(
        ico_output,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )


def ico_sizes(path: Path) -> set[tuple[int, int]]:
    with Image.open(path) as image:
        return set(image.ico.sizes())


if __name__ == "__main__":
    # scripts/ に置くが、資産の入出力はリポジトリ直下の assets/ を基準にする
    assets = Path(__file__).resolve().parent.parent / "assets"
    build_icon_assets(assets / "icon.png", assets / "icon.ico")
