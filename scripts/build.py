"""MosaicTool の実行ファイルを PyInstaller でビルドする

使い方: python scripts/build.py
  --python <path>   : 使用する Python を指定 (既定: 実行中の Python)
  --onedir          : 1 ファイルではなくフォルダ形式で出力 (Windows のみ有効)
  --clean           : build/ dist/ を削除してからビルド
  --uv-version <ver>: 同梱する uv のバージョン (既定: latest)
  --sign-identity   : macOS の署名 ID (既定: 環境変数 MACOS_SIGN_IDENTITY)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import appinfo
import macos_bundle
import macos_sign

UV_ASSETS = {
    "win32": "uv-x86_64-pc-windows-msvc.zip",
    "darwin": "uv-aarch64-apple-darwin.tar.gz",
}


def is_macos() -> bool:
    return sys.platform == "darwin"


def uv_asset_name() -> str:
    asset = UV_ASSETS.get(sys.platform)
    if asset is None:
        appinfo.fail(f"対応していないプラットフォームです: {sys.platform}")
    return asset


def uv_exe_name() -> str:
    return "uv.exe" if sys.platform == "win32" else "uv"


def icon_path() -> Path:
    # .app のバンドルアイコンは .icns しか受け付けない
    name = "icon.icns" if is_macos() else "icon.ico"
    return appinfo.repo_root() / "assets" / name


def uv_dir() -> Path:
    return appinfo.repo_root() / "build" / "uv"


def fetch_uv(version: str) -> Path:
    """自動検出のセットアップに使う uv を取得して build/uv/ にキャッシュする"""
    target = uv_dir() / uv_exe_name()
    if target.is_file():
        return target
    asset = uv_asset_name()
    base = "https://github.com/astral-sh/uv/releases"
    url = (
        f"{base}/latest/download/{asset}"
        if version == "latest"
        else f"{base}/download/{version}/{asset}"
    )
    print(f"-- uv ({version}) を取得します")
    uv_dir().mkdir(parents=True, exist_ok=True)
    archive = uv_dir() / asset
    urllib.request.urlretrieve(url, archive)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(uv_dir())
    else:
        with tarfile.open(archive) as tf:
            # 配布物は uv-<target>/uv の構成なので、実体だけを取り出す
            for member in tf.getmembers():
                if Path(member.name).name == uv_exe_name():
                    member.name = uv_exe_name()
                    tf.extract(member, uv_dir())
                    break
    archive.unlink()
    if not target.is_file():
        appinfo.fail(f"uv を取得できませんでした: {uv_dir()}")
    return target


def worker_script_path() -> Path:
    path = appinfo.repo_root() / "mosaic_tool" / "detect" / "worker_main.py"
    if not path.is_file():
        appinfo.fail(f"検出ワーカーが見つかりません: {path}")
    return path


def _data_arg(source: Path, destination: str) -> str:
    # --add-data の区切りは OS で異なる
    return f"{source}{os.pathsep}{destination}"


def pyinstaller_args(app_name: str, one_dir: bool) -> list[str]:
    root = appinfo.repo_root()
    # macOS は .app バンドルを作るため常に onedir
    mode = "--onedir" if (one_dir or is_macos()) else "--onefile"
    args = [
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        mode,
        "--windowed",              # コンソールウィンドウを出さない
        "--name", app_name,
        "--specpath", "build",     # .spec をリポジトリ直下に置かない
        "--icon", str(icon_path()),
        # mosaic_tool/resources.py が assets/icon.ico を参照する
        "--add-data", _data_arg(root / "assets" / "icon.ico", "assets"),
        # mosaic_tool/detect/paths.py が展開先ルートの uv を参照する
        "--add-data", _data_arg(uv_dir() / uv_exe_name(), "."),
        # ワーカーは venv の Python へスクリプトのパスとして渡すため、
        # PYZ に取り込まれるだけでは足りず .py の実体も同梱する
        "--add-data", _data_arg(worker_script_path(), "mosaic_tool/detect"),
        "--paths", ".",            # mosaic_tool パッケージをリポジトリ直下から解決する
    ]
    if is_macos():
        args += ["--osx-bundle-identifier", f"com.github.kidonaru.{app_name.lower()}"]
    args.append("mosaic_tool/__main__.py")
    return args


def built_app_path(app_name: str, one_dir: bool) -> Path:
    dist = appinfo.repo_root() / "dist"
    if is_macos():
        return dist / f"{app_name}.app"
    if one_dir:
        return dist / app_name / f"{app_name}.exe"
    return dist / f"{app_name}.exe"


def _run_python(python: str, args: list[str]) -> None:
    result = subprocess.run([python, *args], cwd=appinfo.repo_root())
    if result.returncode != 0:
        appinfo.fail(f"コマンドが失敗しました: {python} {' '.join(args)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="実行ファイルをビルドする")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--onedir", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--uv-version", default="latest")
    parser.add_argument("--sign-identity", default=os.environ.get("MACOS_SIGN_IDENTITY", ""))
    args = parser.parse_args()

    app_name = appinfo.read_app_name()
    print(f"== {app_name} v{appinfo.read_version()} をビルドします ({args.python}) ==")

    if args.clean:
        print("-- build/ dist/ を削除します")
        for name in ("build", "dist"):
            shutil.rmtree(appinfo.repo_root() / name, ignore_errors=True)

    print("-- 依存関係をインストールします")
    _run_python(args.python, ["-m", "pip", "install", "--upgrade", "pip"])
    _run_python(args.python, ["-m", "pip", "install", "-r", "requirements.txt"])
    _run_python(args.python, ["-m", "pip", "install", "pyinstaller"])

    fetch_uv(args.uv_version)
    _run_python(args.python, pyinstaller_args(app_name, args.onedir))

    output = built_app_path(app_name, args.onedir)
    if not output.exists():
        appinfo.fail(f"ビルドは完了しましたが実行ファイルが見つかりません: {output}")

    if is_macos():
        # 署名の前に行う(署名後に書き換えると署名が壊れる)
        macos_bundle.patch_info_plist(output)
        print("-- Info.plist に対応ファイル形式を追記しました")
        macos_sign.sign_app(output, args.sign_identity)

    print(f"== 完了: {output} ==")


if __name__ == "__main__":
    main()
