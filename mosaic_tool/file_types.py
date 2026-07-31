"""対応ファイル種別の定数

ビルドスクリプト (scripts/macos_bundle.py) からも参照するため、
Pillow などのサードパーティ依存を持たないモジュールに分離している。
"""
from __future__ import annotations

# 対応する画像拡張子
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
