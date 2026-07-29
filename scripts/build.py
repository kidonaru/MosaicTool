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
from console_utf8 import use_utf8_output

UV_ASSETS = {
    "win32": "uv-x86_64-pc-windows-msvc.zip",
    "darwin": "uv-aarch64-apple-darwin.tar.gz",
}


def is_macos() -> bool:
    return sys.platform == "darwin"


def scripts_dir() -> Path:
    return Path(__file__).resolve().parent


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


def makespec_args(app_name: str, one_dir: bool) -> list[str]:
    """.spec を生成するコマンド

    .spec は一度生成してから編集する(Windows では Qt の OpenSSL バックエンドを
    外すため)。ビルド用の --noconfirm / --clean は makespec が受け付けないので、
    build_args() 側で渡す。
    """
    root = appinfo.repo_root()
    # macOS は .app バンドルを作るため常に onedir
    mode = "--onedir" if (one_dir or is_macos()) else "--onefile"
    args = [
        "-m", "PyInstaller.utils.cliutils.makespec",
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


def spec_path(app_name: str) -> Path:
    # makespec_args() が --specpath build を渡すため build/ 直下に出る
    return appinfo.repo_root() / "build" / f"{app_name}.spec"


def build_args(spec: Path) -> list[str]:
    return ["-m", "PyInstaller", "--noconfirm", "--clean", str(spec)]


def toc_path(app_name: str) -> Path:
    """同梱物の一覧。ビルド後の OpenSSL 検証に使う"""
    return appinfo.repo_root() / "build" / app_name / "EXE-00.toc"


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
    # CI (Windows ランナー) の標準出力は cp1252 で、日本語のまま出すと落ちる
    use_utf8_output()
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
    _run_python(args.python, makespec_args(app_name, args.onedir))

    spec = spec_path(app_name)
    if not spec.is_file():
        appinfo.fail(f"spec が生成されませんでした: {spec}")

    if sys.platform == "win32":
        # Qt の OpenSSL バックエンドは System32 の OpenSSL を引き込み、同梱版と
        # 混在してプロセスごと落ちる。macOS では SecureTransport を使うため不要
        print("-- Qt の OpenSSL バックエンドを除外します")
        _run_python(args.python, [str(scripts_dir() / "exclude_openssl_backend.py"), str(spec)])

    _run_python(args.python, build_args(spec))

    output = built_app_path(app_name, args.onedir)
    if not output.exists():
        appinfo.fail(f"ビルドは完了しましたが実行ファイルが見つかりません: {output}")

    if sys.platform == "win32":
        # libssl と libcrypto が別ディレクトリから拾われると実行時に落ちる
        toc = toc_path(app_name)
        if not toc.is_file():
            # 検証を黙って飛ばすと壊れた exe をそのまま配布してしまうため、ここで止める
            appinfo.fail(
                "TOC が見つからず OpenSSL を検証できません"
                f" (PyInstaller の出力形式が変わった可能性): {toc}"
            )
        print("-- 同梱された OpenSSL を検証します")
        _run_python(args.python, [str(scripts_dir() / "check_bundled_openssl.py"), str(toc)])

    if is_macos():
        # 署名の前に行う(署名後に書き換えると署名が壊れる)
        macos_bundle.patch_info_plist(output)
        print("-- Info.plist に対応ファイル形式を追記しました")
        macos_sign.sign_app(output, args.sign_identity)

    print(f"== 完了: {output} ==")


if __name__ == "__main__":
    main()
