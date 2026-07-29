"""ビルド・リリーススクリプトの共通処理

mosaic_tool/version.py は import せずテキストとして読む
(import すると __pycache__ の古い .pyc を拾うことがあるため)。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 行末を含めないパターンにする(末尾を $ で固定すると CRLF のファイルで一致しない)
_NAME_PATTERN = re.compile(r'(?m)^APP_NAME\s*=\s*"([^"]*)"')
_VERSION_PATTERN = re.compile(r'(?m)^__version__\s*=\s*"([^"]*)"')
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def fail(message: str) -> None:
    """日本語のメッセージを出して終了する"""
    raise SystemExit(f"エラー: {message}")


def repo_root() -> Path:
    """scripts/ に置くが、処理はリポジトリ直下を基準に行う"""
    return Path(__file__).resolve().parent.parent


def version_path() -> Path:
    return repo_root() / "mosaic_tool" / "version.py"


def _read(pattern: re.Pattern[str], label: str) -> str:
    path = version_path()
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        fail(f"mosaic_tool/version.py から {label} を読み取れませんでした: {path}")
    return match.group(1)


def read_app_name() -> str:
    return _read(_NAME_PATTERN, "APP_NAME")


def read_version() -> str:
    return _read(_VERSION_PATTERN, "__version__")


def write_version(target: str) -> None:
    """__version__ の行だけを書き換える(改行コードと他の行は維持する)

    Path.read_text / write_text の newline 引数は Python 3.13 以降のため、
    改行変換を止めるには open() を使う。
    """
    path = version_path()
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    updated = _VERSION_PATTERN.sub(f'__version__ = "{target}"', text, count=1)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(updated)


def next_version(current: str, spec: str) -> str:
    """patch / minor / major 指定を実際のバージョン番号へ解決する"""
    if not _SEMVER.match(current):
        fail(f"現在のバージョンが x.y.z 形式ではありません: {current}")
    major, minor, patch = (int(v) for v in current.split("."))
    keyword = spec.lower()
    if keyword == "major":
        return f"{major + 1}.0.0"
    if keyword == "minor":
        return f"{major}.{minor + 1}.0"
    if keyword == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if not _SEMVER.match(spec):
        fail(f"バージョンは patch / minor / major または x.y.z 形式で指定してください: {spec}")
    return spec


def run(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    """コマンドを実行し、失敗したら日本語メッセージで中断する"""
    result = subprocess.run(
        args, cwd=repo_root(), text=True, encoding="utf-8",
        capture_output=capture,
    )
    if result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        fail(f"コマンドが失敗しました: {' '.join(args)}")
    return result


def git_output(args: list[str]) -> str:
    return run(["git", *args], capture=True).stdout.strip()
