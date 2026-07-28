"""同梱 OpenSSL の先行ロード

PyInstaller の展開先(_MEIPASS)には OpenSSL の DLL が同梱されるが、Qt は SSL を
使う時点で libcrypto / libssl を「ファイル名だけ」で LoadLibrary するため、
探索経路の違いで片方だけが C:\\Windows\\System32 の別バージョンに解決されること
がある。バージョンが混ざると欠けたシンボル(例: CRYPTO_calloc)でプロセスごと
落ちる。起動直後にフルパスでロードしておけば以降は同名モジュールとして再利用
されるため、同梱したペアだけが使われることを保証できる。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

# libssl は libcrypto に依存するため、必ず crypto を先にロードする。
# ファイル名は配布元(公式インストーラ / CPython 同梱)で 2 系統あるため両方見る。
OPENSSL_DLL_NAMES = (
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "libcrypto-3.dll",
    "libssl-3.dll",
)


def preload_bundled_openssl() -> list[Path]:
    """同梱 OpenSSL をフルパスでロードし、ロードできたパスを順に返す"""
    if sys.platform != "win32":
        return []
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if not bundle_dir:
        # ソース実行時は同梱物が無いのでシステムの OpenSSL に任せる
        return []

    loaded: list[Path] = []
    for name in OPENSSL_DLL_NAMES:
        dll = Path(bundle_dir) / name
        if not dll.is_file():
            continue
        try:
            ctypes.WinDLL(str(dll))
        except OSError:
            # ここで止めると起動できなくなる。Qt の既定探索に委ねて続行する
            continue
        loaded.append(dll)
    return loaded
