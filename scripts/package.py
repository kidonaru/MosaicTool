"""MosaicTool の配布用 zip を作成する

使い方: python scripts/package.py
  --python <path> : 使用する Python を指定 (build.py へ透過)
  --clean         : build/ dist/ を削除してからビルド (build.py へ透過)
  --skip-build    : 既にある dist/ の成果物を使う
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import appinfo
import build

PLATFORM_TAGS = {"win32": "win-x64", "darwin": "mac-arm64"}


def platform_tag() -> str:
    tag = PLATFORM_TAGS.get(sys.platform)
    if tag is None:
        appinfo.fail(f"対応していないプラットフォームです: {sys.platform}")
    return tag


def package_name(app_name: str, version: str) -> str:
    """配布物の命名規則はこのスクリプトを唯一の情報源とする"""
    return f"{app_name}-v{version}-{platform_tag()}"


def emit_github_output(name: str, value: str) -> None:
    """GitHub Actions から呼ばれた場合に値を受け渡す"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def stage(app: Path, stage_dir: Path) -> None:
    """展開時に中身が散らばらないよう、zip 内へトップレベルフォルダを 1 つ作る"""
    readme = appinfo.repo_root() / "README.md"
    if not readme.is_file():
        appinfo.fail(f"同梱する README.md が見つかりません: {readme}")
    stage_dir.mkdir(parents=True, exist_ok=True)
    if app.is_dir():
        # .app はシンボリックリンクと実行ビットを保ったままコピーする
        shutil.copytree(app, stage_dir / app.name, symlinks=True)
    else:
        shutil.copy2(app, stage_dir / app.name)
    shutil.copy2(readme, stage_dir / readme.name)


def make_zip(stage_root: Path, stage_dir: Path, zip_path: Path) -> None:
    """配布用 zip を作る

    macOS は ditto を使う。zipfile では .app の実行ビットとシンボリックリンクが
    壊れ、展開したアプリが起動しなくなる。
    """
    if sys.platform == "darwin":
        result = subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
             str(stage_dir), str(zip_path)]
        )
        if result.returncode != 0:
            appinfo.fail(f"ditto による zip 作成に失敗しました: {zip_path}")
        return
    # Windows の Compress-Archive は区切りに \ を書き ZIP 仕様に反するため、
    # エントリ名を / で明示して作る
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(p for p in stage_dir.rglob("*") if p.is_file()):
            zf.write(file, file.relative_to(stage_root).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description="配布用 zip を作成する")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    root = appinfo.repo_root()
    app_name = appinfo.read_app_name()
    name = package_name(app_name, appinfo.read_version())
    emit_github_output("package_name", name)

    # 同梱する README はビルド前に確認する(ビルド後に失敗すると数分を無駄にする)
    if not (root / "README.md").is_file():
        appinfo.fail(f"同梱する README.md が見つかりません: {root / 'README.md'}")

    print(f"== {name} をパッケージします ==")

    if not args.skip_build:
        build_args = [sys.executable, str(root / "scripts" / "build.py"),
                      "--python", args.python]
        if args.clean:
            build_args.append("--clean")
        if subprocess.run(build_args, cwd=root).returncode != 0:
            appinfo.fail("ビルドに失敗しました")

    app = build.built_app_path(app_name, one_dir=False)
    if not app.exists():
        appinfo.fail(f"ビルドした成果物が見つかりません: {app}")

    # ステージング先ごと作り直して、古いバージョンの残骸を zip に混ぜない
    stage_root = root / "build" / "package"
    shutil.rmtree(stage_root, ignore_errors=True)
    stage_dir = stage_root / name
    stage(app, stage_dir)

    # 旧バージョンの zip を消す(release.yml が dist/*.zip で拾うため)
    for old in (root / "dist").glob("*.zip"):
        old.unlink()
    zip_path = root / "dist" / f"{name}.zip"
    make_zip(stage_root, stage_dir, zip_path)

    print(f"== 完了: {zip_path} ==")


if __name__ == "__main__":
    main()
