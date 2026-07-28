"""リポジトリ直下と PyInstaller 展開先の解決

同梱リソース(アイコン, uv, ワーカー本体)を探すための基準を一元化する。
Qt に依存しないため、GUI を起動しないテストからも使える。
"""
from __future__ import annotations

import sys
from pathlib import Path


def repo_root() -> Path:
    """ソース実行時のリポジトリ直下(このファイルは mosaic_tool/ にある)"""
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """同梱リソースの基準

    PyInstaller は展開先を sys._MEIPASS で知らせる(onefile なら一時ディレクトリ、
    onedir なら _internal / .app の Contents/Frameworks)。パッケージ本体は PYZ に
    取り込まれ __file__ が実在しないため、同梱物の探索には必ずこちらを使う。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return repo_root()
