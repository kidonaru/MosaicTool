"""自動検出まわりのパス解決

models/ と runtime/ は exe と同じ場所に置く(展開したフォルダごと持ち運べるように)。
同梱リソース(uv.exe, worker_main.py)は onefile では展開先の一時ディレクトリに
現れるため、基準が別になる。
"""
from __future__ import annotations

import sys
from pathlib import Path

from mosaic_tool.bundle import bundle_dir, repo_root

MODELS_DIR_NAME = "models"
RUNTIME_DIR_NAME = "runtime"
MODEL_SUFFIX = ".pt"
# runtime\ へコピーするワーカーのファイル名(venv の Python へ渡すため実体が要る)
WORKER_SCRIPT_NAME = "detect_worker.py"
UV_EXE_NAME = "uv.exe"


def base_dir() -> Path:
    """models/ runtime/ を置く基準ディレクトリ

    frozen(PyInstaller)では exe と同じ場所、ソース実行ではリポジトリ直下。
    """
    if getattr(sys, "frozen", False):
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
    return runtime_dir() / "Scripts" / "python.exe"


def is_runtime_ready() -> bool:
    """推論環境(venv)が構築済みか"""
    return venv_python().is_file()


def bundled_uv_path() -> Path:
    return bundle_dir() / UV_EXE_NAME


def worker_script_source() -> Path:
    """同梱されたワーカー本体(.py の実体。PYZ 内のモジュールでは代用できない)"""
    return bundle_dir() / "mosaic_tool" / "detect" / "worker_main.py"


def worker_script_installed() -> Path:
    return runtime_dir() / WORKER_SCRIPT_NAME
