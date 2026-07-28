"""同梱された OpenSSL DLL の組み合わせをビルド後に検証する

libssl は libcrypto と強く結びついており、片方だけ別バージョンが混ざると欠けた
シンボル(例: CRYPTO_calloc)でプロセスごと起動に失敗する。PyInstaller は依存 DLL
をビルド機の探索順で 1 つずつ解決するため、PATH の状態によっては libssl と
libcrypto を別ディレクトリから拾ってしまう。同じ名前系統のペアが同一ディレクトリ
由来であることを確かめ、混在していればビルドを失敗させる。

使い方: python scripts/check_bundled_openssl.py build/<AppName>/EXE-00.toc
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# libcrypto-3-x64.dll / libssl-3.dll のように「種別 + 系統」で命名されている
_OPENSSL_NAME = re.compile(r"^(libssl|libcrypto)(.*\.dll)$", re.IGNORECASE)
_BINARY_TYPECODE = "BINARY"


def _iter_toc_entries(node):
    """TOC(入れ子のタプル)から (配置名, 取得元, 種別) の 3 要素だけを拾う"""
    if not isinstance(node, (list, tuple)):
        return
    if len(node) == 3 and all(isinstance(item, str) for item in node):
        yield node
        return
    for child in node:
        yield from _iter_toc_entries(child)


def openssl_sources(toc_text: str) -> dict[str, dict[str, Path]]:
    """{系統: {種別: 取得元パス}} を返す(系統は "-3-x64.dll" など)"""
    found: dict[str, dict[str, Path]] = {}
    for dest, source, typecode in _iter_toc_entries(ast.literal_eval(toc_text)):
        if typecode != _BINARY_TYPECODE:
            continue
        matched = _OPENSSL_NAME.match(Path(dest).name)
        if matched:
            kind, family = matched.group(1).lower(), matched.group(2).lower()
            found.setdefault(family, {})[kind] = Path(source)
    return found


def find_mismatches(toc_text: str) -> list[str]:
    """libssl と libcrypto の取得元が食い違っている系統の説明を返す"""
    messages = []
    for family, sources in sorted(openssl_sources(toc_text).items()):
        if len(sources) < 2:
            # 片方しか同梱されていない系統は組み合わせが生じないので対象外
            continue
        directories = {str(p.parent).casefold() for p in sources.values()}
        if len(directories) > 1:
            detail = ", ".join(f"{kind}{family} <- {src}" for kind, src in sorted(sources.items()))
            messages.append(f"{family}: 取得元ディレクトリが一致しません ({detail})")
    return messages


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"使い方: {Path(argv[0]).name} <EXE-00.toc>", file=sys.stderr)
        return 2
    toc_text = Path(argv[1]).read_text(encoding="utf-8")
    mismatches = find_mismatches(toc_text)
    if mismatches:
        print("同梱された OpenSSL の組み合わせが不正です:", file=sys.stderr)
        for message in mismatches:
            print(f"  - {message}", file=sys.stderr)
        print(
            "ビルド機の PATH を整理し、libssl と libcrypto が同じ場所から"
            "拾われるようにしてから再ビルドしてください。",
            file=sys.stderr,
        )
        return 1
    for family, sources in sorted(openssl_sources(toc_text).items()):
        print(f"OpenSSL{family}: {next(iter(sources.values())).parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
