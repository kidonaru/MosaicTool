"""macOS の .app バンドルへの後処理

PyInstaller の CLI では CFBundleDocumentTypes を指定できないため、
生成後の Info.plist へ追記する。署名の前に行うこと(後から書き換えると
署名が壊れる)。
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import appinfo

# 対応拡張子はアプリ本体を唯一の情報源にする。python scripts/build.py の形で
# 起動されるとリポジトリ直下が sys.path に入らないため、ここで通しておく
sys.path.insert(0, str(appinfo.repo_root()))

# ビルド用 Python には Pillow 等が入っていないため、依存を持たない定数モジュールから読む
from mosaic_tool.file_types import IMAGE_EXTS  # noqa: E402


def document_types() -> list[dict]:
    """Finder の「このアプリケーションで開く」に出す対応形式"""
    return [
        {
            "CFBundleTypeName": "画像ファイル",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": sorted(
                ext.lstrip(".") for ext in IMAGE_EXTS
            ),
        }
    ]


def patch_info_plist(app: Path) -> None:
    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        appinfo.fail(f"Info.plist が見つかりません: {plist}")
    data = plistlib.loads(plist.read_bytes())
    data["CFBundleDocumentTypes"] = document_types()
    plist.write_bytes(plistlib.dumps(data))
