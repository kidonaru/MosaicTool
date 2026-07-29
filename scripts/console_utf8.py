"""ビルド用スクリプトの出力を UTF-8 に固定する

CI (GitHub Actions の Windows ランナー) の標準出力は cp1252 で、日本語を
そのまま print すると UnicodeEncodeError になりビルドが途中で止まる。
特に失敗時のメッセージが出せずに落ちると、本来のビルドエラーが覆い隠される。
"""
from __future__ import annotations

import sys


def use_utf8_output() -> None:
    """標準出力・標準エラーを UTF-8 に切り替える"""
    for stream in (sys.stdout, sys.stderr):
        # pytest 等が差し替えたストリームには reconfigure が無いことがある
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
