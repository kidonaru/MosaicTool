"""mosaic_tool/version.py のバージョンを更新してコミットする

使い方: python scripts/bump.py patch
  patch / minor / major : 現在のバージョンから該当箇所を 1 上げる
  x.y.z                 : そのバージョンを直接指定する
  --dry-run             : 検証だけ行い、ファイル書き換えとコミットは行わない
"""
from __future__ import annotations

import argparse

import appinfo


def resolve_target(spec: str) -> tuple[str, str]:
    """(現在のバージョン, 更新後のバージョン) を返す"""
    current = appinfo.read_version()
    target = appinfo.next_version(current, spec)
    if current == target:
        appinfo.fail(f"バージョンは既に {target} です")
    return current, target


def ensure_clean_worktree() -> None:
    """version.py 以外の未コミット変更があると無関係な変更を巻き込むため中断する"""
    lines = appinfo.git_output(["status", "--porcelain", "--untracked-files=no"])
    dirty = [
        line for line in lines.splitlines()
        if line.strip() and not line.endswith("mosaic_tool/version.py")
    ]
    if dirty:
        appinfo.fail(
            "未コミットの変更があります。コミットまたは退避してから実行してください:\n"
            + "\n".join(dirty)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="バージョンを更新してコミットする")
    parser.add_argument("version", nargs="?", default="patch")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ensure_clean_worktree()
    current, target = resolve_target(args.version)

    if args.dry_run:
        print("== dry-run: 検証のみ実行しました ==")
        print(f"更新されるバージョン: v{current} -> v{target}")
        print("実行される操作: mosaic_tool/version.py の書き換え / git commit")
        return

    appinfo.write_version(target)
    appinfo.run(["git", "add", "--", str(appinfo.version_path())])
    appinfo.run(["git", "commit", "-m", f"chore(release): v{target} にバージョンを更新"])
    print(f"== v{current} -> v{target} をコミットしました ==")
    print("次: just tag (または python scripts/tag.py)")


if __name__ == "__main__":
    main()
