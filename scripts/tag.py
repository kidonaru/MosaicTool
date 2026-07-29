"""mosaic_tool/version.py のバージョンでタグを作成して push する

使い方: python scripts/tag.py
  --remote <name> : push 先のリモート (既定: origin)
  --branch <name> : リリース元として許可するブランチ (既定: main)
  --dry-run       : 検証だけ行い、push / タグ作成は行わない
"""
from __future__ import annotations

import argparse
import re

import appinfo


def actions_url(remote_url: str) -> str | None:
    """リモート URL から Actions のページを組み立てる(GitHub 以外は None)"""
    url = re.sub(r"\.git$", "", remote_url.strip())
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    return f"{url}/actions" if url.startswith("https://github.com/") else None


def main() -> None:
    parser = argparse.ArgumentParser(description="バージョンのタグを作成して push する")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tag = f"v{appinfo.read_version()}"

    # 意図しないブランチからリリースしないことを確認する
    branch = appinfo.git_output(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch != args.branch:
        appinfo.fail(
            f"現在のブランチは {branch} です。"
            f"{args.branch} から実行するか --branch で許可してください"
        )

    if appinfo.git_output(["status", "--porcelain", "--untracked-files=no"]):
        appinfo.fail("未コミットの変更があります。タグはコミット済みの状態で作成してください")

    # fetch してからローカルを見ることで、リモートにあるタグの重複も検出する
    appinfo.run(["git", "fetch", "--tags", "--quiet", args.remote])
    if appinfo.git_output(["tag", "--list", tag]):
        appinfo.fail(
            f"タグ {tag} は既に存在します。mosaic_tool/version.py のバージョンを上げてください"
        )

    if args.dry_run:
        print("== dry-run: 検証のみ実行しました ==")
        print(f"作成されるタグ: {tag}")
        print(
            f"実行される操作: git push {args.remote} HEAD / "
            f"git tag -a {tag} / git push {args.remote} {tag}"
        )
        return

    # タグだけを push してもコミットが無いとビルドできないため、先に HEAD を push する
    appinfo.run(["git", "push", args.remote, "HEAD"])
    appinfo.run(["git", "tag", "-a", tag, "-m", tag])
    try:
        appinfo.run(["git", "push", args.remote, tag])
    except SystemExit:
        # push に失敗したままローカルのタグが残ると、次回の重複チェックで止まる
        appinfo.run(["git", "tag", "-d", tag])
        appinfo.fail(f"タグの push に失敗しました。作成したローカルのタグ {tag} は削除しました")

    print(f"== {tag} を push しました ==")
    url = actions_url(appinfo.git_output(["remote", "get-url", args.remote]))
    if url:
        print(f"release ワークフローの進行: {url}")


if __name__ == "__main__":
    main()
