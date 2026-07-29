"""同梱された OpenSSL 関連の同梱物をビルド後に検証する

検証は 2 つ:

1. Qt の OpenSSL TLS バックエンドが同梱されていないこと。これが入っていると Qt が
   System32 の OpenSSL を引き込み、同梱版と混在して落ちる(exclude_openssl_backend.py
   が除外しているはずのもの)。
2. libssl と libcrypto が同一ディレクトリ由来であること。両者は強く結びついており、
   片方だけ別バージョンが混ざると欠けたシンボル(例: CRYPTO_calloc)で落ちる。
   PyInstaller は依存 DLL をビルド機の探索順で 1 つずつ解決するため、PATH の状態に
   よっては別ディレクトリから拾ってしまう。

2 つは独立した検証で、どちらか一方でも引っかかればビルドを失敗させる。

使い方: python scripts/check_bundled_openssl.py build/<AppName>/EXE-00.toc
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path, PureWindowsPath

from console_utf8 import use_utf8_output

# libcrypto-3-x64.dll / libssl-3.dll のように「種別 + 系統」で命名されている
_OPENSSL_NAME = re.compile(r"^(libssl|libcrypto)(.*\.dll)$", re.IGNORECASE)
_BINARY_TYPECODE = "BINARY"
# Qt の OpenSSL TLS バックエンド(同梱してはいけない。理由は exclude_openssl_backend.py)
_QT_OPENSSL_BACKEND = "qopensslbackend"


def _iter_toc_entries(node):
    """TOC(入れ子のタプル)から (配置名, 取得元, 種別) の 3 要素だけを拾う"""
    if not isinstance(node, (list, tuple)):
        return
    if len(node) == 3 and all(isinstance(item, str) for item in node):
        yield node
        return
    for child in node:
        yield from _iter_toc_entries(child)


def openssl_sources(toc_text: str) -> dict[str, dict[str, PureWindowsPath]]:
    """{系統: {種別: 取得元パス}} を返す(系統は "-3-x64.dll" など)

    検証対象は必ず Windows ビルドの TOC なので、パスの解釈も Windows 固定にする
    (POSIX 上で Path を使うと "C:\\a\\b.dll" が 1 つの名前として扱われ、
    取得元ディレクトリの比較が常に一致してしまう)。
    """
    found: dict[str, dict[str, PureWindowsPath]] = {}
    for dest, source, typecode in _iter_toc_entries(ast.literal_eval(toc_text)):
        if typecode != _BINARY_TYPECODE:
            continue
        matched = _OPENSSL_NAME.match(PureWindowsPath(dest).name)
        if matched:
            kind, family = matched.group(1).lower(), matched.group(2).lower()
            found.setdefault(family, {})[kind] = PureWindowsPath(source)
    return found


def qt_openssl_backends(toc_text: str) -> list[str]:
    """同梱されている Qt の OpenSSL TLS バックエンドの配置名を返す

    このプラグインが入っていると Qt が System32 の OpenSSL を引き込むため、
    exclude_openssl_backend.py で除外されているはずのもの。
    """
    return [
        dest
        for dest, _source, typecode in _iter_toc_entries(ast.literal_eval(toc_text))
        if typecode == _BINARY_TYPECODE
        and _QT_OPENSSL_BACKEND in PureWindowsPath(dest).name.lower()
    ]


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
    use_utf8_output()
    if len(argv) != 2:
        print(f"使い方: {Path(argv[0]).name} <EXE-00.toc>", file=sys.stderr)
        return 2
    toc_text = Path(argv[1]).read_text(encoding="utf-8")
    backends = qt_openssl_backends(toc_text)
    if backends:
        print("Qt の OpenSSL TLS バックエンドが同梱されています:", file=sys.stderr)
        for dest in backends:
            print(f"  - {dest}", file=sys.stderr)
        print(
            "exclude_openssl_backend.py による .spec の除外処理が効いていません。",
            file=sys.stderr,
        )
        return 1

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
