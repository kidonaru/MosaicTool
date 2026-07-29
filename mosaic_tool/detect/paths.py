"""自動検出まわりのパス解決

models/ と runtime/ を置く場所は OS で異なる。
Windows は実行ファイルの隣(展開したフォルダごと持ち運べるように)、
macOS は .app を /Applications へ移しても書ける Application Support 配下。
同梱リソース(uv, worker_main.py)は PyInstaller の展開先が基準になる。
"""
from __future__ import annotations

import sys
from pathlib import Path

from mosaic_tool.bundle import bundle_dir, repo_root
from mosaic_tool.version import APP_NAME

MODELS_DIR_NAME = "models"
RUNTIME_DIR_NAME = "runtime"
MODEL_SUFFIX = ".pt"
# runtime/ へコピーするワーカーのファイル名(venv の Python へ渡すため実体が要る)
WORKER_SCRIPT_NAME = "detect_worker.py"


def _is_windows() -> bool:
    return sys.platform == "win32"


def user_data_dir() -> Path:
    """macOS でユーザーデータを置く場所(.app の中を汚さない)"""
    return Path.home() / "Library" / "Application Support" / APP_NAME


def uv_exe_name() -> str:
    return "uv.exe" if _is_windows() else "uv"


def base_dir() -> Path:
    """models/ runtime/ を置く基準ディレクトリ

    frozen(PyInstaller)では Windows が実行ファイルの隣、macOS が
    Application Support 配下。ソース実行ではどちらもリポジトリ直下。
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return user_data_dir()
        return Path(sys.executable).resolve().parent
    return repo_root()


def models_dir() -> Path:
    return base_dir() / MODELS_DIR_NAME


def runtime_dir() -> Path:
    return base_dir() / RUNTIME_DIR_NAME


def model_files() -> list[Path]:
    """models/ に置かれた検出モデルをファイル名順に返す"""
    directory = models_dir()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(f"*{MODEL_SUFFIX}") if p.is_file())


def venv_python() -> Path:
    """venv の Python(レイアウトが OS で異なる)"""
    if _is_windows():
        return runtime_dir() / "Scripts" / "python.exe"
    return runtime_dir() / "bin" / "python"


def is_runtime_ready() -> bool:
    """推論環境(venv)が構築済みか"""
    return venv_python().is_file()


def bundled_uv_path() -> Path:
    return bundle_dir() / uv_exe_name()


def worker_script_source() -> Path:
    """同梱されたワーカー本体(.py の実体。PYZ 内のモジュールでは代用できない)"""
    return bundle_dir() / "mosaic_tool" / "detect" / "worker_main.py"


def worker_script_installed() -> Path:
    return runtime_dir() / WORKER_SCRIPT_NAME
