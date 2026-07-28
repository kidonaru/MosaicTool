"""自動検出まわりのパス解決

models/ と runtime/ は exe と同じ場所に置く(展開したフォルダごと持ち運べるように)。
同梱リソース(uv.exe, worker_main.py)は onefile では展開先の一時ディレクトリに
現れるため、基準が別になる。
"""
from __future__ import annotations

import sys
from pathlib import Path

MODELS_DIR_NAME = "models"
RUNTIME_DIR_NAME = "runtime"
MODEL_SUFFIX = ".pt"
# runtime\ へコピーするワーカーのファイル名(venv の Python へ渡すため実体が要る)
WORKER_SCRIPT_NAME = "detect_worker.py"
UV_EXE_NAME = "uv.exe"


def _repo_root() -> Path:
    """ソース実行時のリポジトリ直下(このファイルは mosaic_tool/detect/ にある)"""
    return Path(__file__).resolve().parents[2]


def base_dir() -> Path:
    """models/ runtime/ を置く基準ディレクトリ

    frozen(PyInstaller)では exe と同じ場所、ソース実行ではリポジトリ直下。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _repo_root()


def _bundle_dir() -> Path:
    """同梱リソースの基準

    PyInstaller は展開先を sys._MEIPASS で知らせる(onefile なら一時ディレクトリ、
    onedir なら _internal)。パッケージ本体は PYZ に取り込まれ __file__ が実在
    しないため、同梱物の探索には必ずこちらを使う。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return _repo_root()


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
    return _bundle_dir() / UV_EXE_NAME


def worker_script_source() -> Path:
    """同梱されたワーカー本体(.py の実体。PYZ 内のモジュールでは代用できない)"""
    return _bundle_dir() / "mosaic_tool" / "detect" / "worker_main.py"


def worker_script_installed() -> Path:
    return runtime_dir() / WORKER_SCRIPT_NAME
