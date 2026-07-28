"""MosaicToolの配布用アイコン資産を生成する。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
# .icns に必ず含まれていてほしい (幅, 高さ, 倍率)。Pillow は 1024 まで書き出すが、
# 読み戻したときの表現は Pillow のバージョンで変わりうるため下限だけを固定する
ICNS_REQUIRED_SIZES = frozenset(
    {(512, 512, 1), (256, 256, 1), (128, 128, 1), (512, 512, 2), (256, 256, 2)}
)


def build_icon_assets(source: Path, ico_output: Path, icns_output: Path) -> None:
    with Image.open(source) as image:
        if image.width != image.height:
            raise ValueError("PNGマスターは正方形である必要があります")
        master = image.convert("RGBA")

    master.save(
        ico_output,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )
    # Pillow の ICNS 書き出しは純 Python 実装のため、macOS 以外でも生成できる
    master.save(icns_output, format="ICNS")


def ico_sizes(path: Path) -> set[tuple[int, int]]:
    with Image.open(path) as image:
        return set(image.ico.sizes())


def icns_sizes(path: Path) -> set[tuple[int, int, int]]:
    with Image.open(path) as image:
        return {tuple(size) for size in image.info["sizes"]}


if __name__ == "__main__":
    # scripts/ に置くが、資産の入出力はリポジトリ直下の assets/ を基準にする
    assets = Path(__file__).resolve().parent.parent / "assets"
    build_icon_assets(assets / "icon.png", assets / "icon.ico", assets / "icon.icns")
