"""生成された .spec から Qt の OpenSSL TLS バックエンドを除外する

Qt は利用可能な TLS バックエンドを列挙する際、tls プラグインを全て LoadLibrary
する。qopensslbackend.dll は OpenSSL を静的インポートしており、その解決先は
C:\\Windows\\System32 に固定されている(PATH も AddDllDirectory も効かない)。
その結果、同梱 OpenSSL とは別バージョンが同じモジュール名でプロセスに入り込み、
シンボル欠落(CRYPTO_calloc)でプロセスごと落ちる。

プラグインを同梱しなければ Qt は Windows ネイティブの SChannel を使い、OpenSSL を
一切読み込まなくなる。PyInstaller の CLI にはバイナリを除外する指定が無いため、
pyi-makespec が生成した .spec に除外処理を差し込む。

使い方: python scripts/exclude_openssl_backend.py build/<AppName>.spec
"""
from __future__ import annotations

import sys
from pathlib import Path

EXCLUDED_PLUGIN = "qopensslbackend"
# pyi-makespec が必ず出力する行。ここに来るまでに a.binaries が確定している
_ANCHOR = "pyz = PYZ("
# 差し込む行そのもの。適用済み判定にも使う(プラグイン名だけで探すと、spec の
# 別の箇所にたまたま同じ名前が現れたときに適用済みと誤判定してしまう)
_FILTER_LINE = f'a.binaries = [b for b in a.binaries if "{EXCLUDED_PLUGIN}" not in b[0].lower()]'
_PATCH = f'''# Qt の OpenSSL TLS バックエンドは同梱しない
# (System32 の OpenSSL を引き込み、同梱版と混在してプロセスごと落ちるため)
{_FILTER_LINE}

'''


def patch(spec_text: str) -> str:
    """spec に除外処理を差し込む(既に差し込み済みならそのまま返す)"""
    if _FILTER_LINE in spec_text:
        return spec_text
    if _ANCHOR not in spec_text:
        raise ValueError(
            f"spec に {_ANCHOR!r} が見つからず除外処理を差し込めません"
            "(PyInstaller の生成形式が変わった可能性)"
        )
    return spec_text.replace(_ANCHOR, _PATCH + _ANCHOR, 1)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"使い方: {Path(argv[0]).name} <spec ファイル>", file=sys.stderr)
        return 2
    spec_path = Path(argv[1])
    spec_text = spec_path.read_text(encoding="utf-8")
    patched = patch(spec_text)
    if patched == spec_text:
        print(f"{spec_path.name}: 除外処理は適用済みです")
        return 0
    spec_path.write_text(patched, encoding="utf-8")
    print(f"{spec_path.name}: Qt の OpenSSL バックエンドを除外しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
