"""ファイル入出力: 画像の列挙、_mc 命名、読み込み/保存"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, PngImagePlugin

# 対応する画像拡張子
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def mc_file_path(src: Path) -> Path:
    """ファイル単体の保存先: 同じ場所の 名前_mc.拡張子"""
    return src.with_name(f"{src.stem}_mc{src.suffix}")


def mc_folder_path(folder: Path) -> Path:
    """フォルダの保存先: 隣の フォルダ名_mc"""
    return folder.with_name(folder.name + "_mc")


def list_images(folder: Path) -> list[Path]:
    """フォルダ直下の対応画像を名前順で列挙する"""
    return sorted(p for p in folder.iterdir() if p.is_file() and is_image_file(p))


def load_image(path: Path) -> Image.Image:
    """画像を読み込む(失敗時は例外を送出、呼び出し側で警告表示)"""
    img = Image.open(path)
    img.load()
    return img


JPEG_EXTS = {".jpg", ".jpeg"}

# info から保存時にそのまま引き継ぐメタ情報のキー(未対応の形式では無視される)
_META_KEYS = ("exif", "icc_profile", "xmp", "dpi")

# JPEG の APP1 セグメントに収まる最大バイト数。超えると Pillow が保存に失敗する
_JPEG_APP1_MAX = 65533


def _meta_kwargs(img: Image.Image, suffix: str) -> dict:
    """元画像の info から保存時に引き継ぐメタ情報を組み立てる"""
    kwargs = {k: img.info[k] for k in _META_KEYS if img.info.get(k)}
    if suffix in JPEG_EXTS:
        # 巨大な Exif/XMP は保存自体が失敗するため、メタ情報を捨てて保存を優先する
        for key in ("exif", "xmp"):
            if len(kwargs.get(key, b"")) > _JPEG_APP1_MAX:
                del kwargs[key]
    elif suffix == ".png":
        # PNG のテキストチャンクは pnginfo 経由でしか引き継げない
        text_items = {k: v for k, v in img.info.items() if isinstance(v, str)}
        if text_items:
            pnginfo = PngImagePlugin.PngInfo()
            for key, value in text_items.items():
                pnginfo.add_text(key, value)
            kwargs["pnginfo"] = pnginfo
    return kwargs


def save_image(img: Image.Image, dest: Path) -> None:
    """元と同形式で保存する。JPG は品質 95。親フォルダは自動作成

    Exif / ICC プロファイル等のメタ情報は元画像から引き継ぐ。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = dest.suffix.lower()
    if suffix in JPEG_EXTS:
        if img.mode not in ("RGB", "L"):
            # JPEG はアルファ非対応のため変換する
            img = img.convert("RGB")
        img.save(dest, quality=95, **_meta_kwargs(img, suffix))
    else:
        img.save(dest, **_meta_kwargs(img, suffix))
