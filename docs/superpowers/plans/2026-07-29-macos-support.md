# macOS (Apple Silicon) 版対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MosaicTool を macOS (Apple Silicon) 向けにビルド・署名・配布できるようにし、自動検出を MPS で動かす。

**Architecture:** OS 差分は境界モジュール（`detect/paths.py` / `detect/runtime.py` / `resources.py`）内の名前付き関数に閉じ込め、描画・編集ロジックには一切触れない。PowerShell のビルド・リリーススクリプト 5 本を Python へ移植して 1 系統に統合し、GitHub Actions は Windows / macOS の 2 ビルドジョブと 1 リリースジョブに再構成する。

**Tech Stack:** Python 3.10+ / PySide6 / Pillow / PyInstaller / uv / pytest / just / GitHub Actions

**設計書:** `docs/superpowers/specs/2026-07-29-macos-support-design.md`

## Global Constraints

- 対象は Apple Silicon (arm64) のみ。Intel Mac と universal2 は対象外
- Windows 版の既存挙動は変えない。特に `base_dir()` が実行ファイルの隣を指すことは維持する（既存ユーザーの `models/` `runtime/` が見えなくなるため）
- macOS のデータ配置は `~/Library/Application Support/MosaicTool/`
- 配布物名は `MosaicTool-v<バージョン>-mac-arm64.zip`（Windows は `MosaicTool-v<バージョン>-win-x64.zip`）
- macOS のバンドル形式は onedir (`.app`)。onefile は使わない
- アプリ名とバージョンの唯一の情報源は `mosaic_tool/version.py`
- 配布物の命名規則の唯一の情報源は `scripts/package.py`
- コメントとエラーメッセージは日本語で書く
- 検索は `rg`、ファイル探索は `fd` を使う
- サードパーティ GitHub Action は commit SHA で固定する
- テストは全 OS で実行できること（macOS 専用の外部コマンドに依存するテストを書かない）
- 各タスクの最後に必ずコミットする

## 設計書からの逸脱（実装上の都合による確定事項）

1. **署名は PyInstaller の `--codesign-identity` を使わない。** `Info.plist` に `CFBundleDocumentTypes` を後から追記すると署名が壊れるため、「ビルド → Info.plist パッチ → 署名」の順に手動で `codesign` する。
2. **`bundle_dir()` の重複を避けるため `mosaic_tool/bundle.py` を新設する。** 設計書は「新規モジュールを作らない」としているが、これは OS 分岐用モジュールを作らないという意味であり、`resources.py` と `detect/paths.py` に同一実装を 2 つ置くほうが害が大きい。

## File Structure

| ファイル | 責務 | 種別 |
|---|---|---|
| `mosaic_tool/bundle.py` | リポジトリ直下と PyInstaller 展開先の解決（Qt 非依存） | 新規 |
| `mosaic_tool/resources.py` | 同梱アイコンの解決 | 変更 |
| `mosaic_tool/detect/paths.py` | データ配置基準・venv の Python・uv の実行ファイル名の OS 分岐 | 変更 |
| `mosaic_tool/detect/runtime.py` | インストールコマンドの OS 分岐・推論デバイス解決・uv の実行権限 | 変更 |
| `mosaic_tool/detect/setup_dialog.py` | macOS では CPU/GPU 選択を出さない | 変更 |
| `mosaic_tool/app.py` | `resolve_device()` を使う | 変更 |
| `mosaic_tool/application.py` | `QFileOpenEvent`（Finder からの「開く」）を処理する `QApplication` | 新規 |
| `mosaic_tool/__main__.py` | `MosaicApplication` を使う | 変更 |
| `scripts/appinfo.py` | `version.py` の読み取りとリポジトリ直下の解決（全スクリプト共通） | 新規 |
| `scripts/bump.py` / `scripts/tag.py` | `bump.ps1` / `tag.ps1` の移植 | 新規 |
| `scripts/build.py` | `build.ps1` の移植 + macOS 分岐 + Info.plist パッチ + 署名 | 新規 |
| `scripts/package.py` | `package.ps1` の移植 + macOS 分岐 + 公証 + staple | 新規 |
| `scripts/macos_bundle.py` | `Info.plist` の document types 生成とパッチ | 新規 |
| `scripts/macos_sign.py` | `codesign` / `notarytool` / `stapler` の実行 | 新規 |
| `scripts/entitlements.plist` | Hardened Runtime 用 entitlements | 新規 |
| `scripts/icon_assets.py` | `.ico` に加えて `.icns` を生成 | 変更 |
| `scripts/*.ps1` | 削除（Python へ移植済み） | 削除 |
| `justfile` | レシピを `python scripts/*.py` に統一 | 変更 |
| `.github/workflows/release.yml` | meta / build-windows / build-macos / release の 4 ジョブ構成 | 変更 |

---

### Task 1: 同梱リソースの基準を `bundle.py` へ集約する

`detect/paths.py` の `_repo_root()` / `_bundle_dir()` と `resources.py` の `__file__` 基準の解決を 1 か所にまとめる。`resources.py` は現在 `__file__` を使っているが、PyInstaller ではパッケージが PYZ に取り込まれて `__file__` が実在しないため、`.app` 内でアイコンを解決できない。

**Files:**
- Create: `mosaic_tool/bundle.py`
- Create: `tests/test_bundle.py`
- Modify: `mosaic_tool/resources.py`
- Modify: `mosaic_tool/detect/paths.py`
- Test: `tests/test_resources.py`, `tests/test_detect_paths.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `mosaic_tool.bundle.repo_root() -> Path` — ソース実行時のリポジトリ直下
  - `mosaic_tool.bundle.bundle_dir() -> Path` — 同梱リソースの基準（frozen なら `sys._MEIPASS`、それ以外は `repo_root()`）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_bundle.py` を新規作成する。

```python
"""同梱リソースの基準ディレクトリ解決の検証"""
import sys

from mosaic_tool import bundle


def test_repo_root_contains_the_package():
    # このファイルは mosaic_tool/ にあるため、1 つ上がリポジトリ直下
    assert (bundle.repo_root() / "mosaic_tool").is_dir()


def test_bundle_dir_is_repo_root_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert bundle.bundle_dir() == bundle.repo_root()


def test_bundle_dir_is_meipass_when_frozen(monkeypatch, tmp_path):
    # PyInstaller は展開先を sys._MEIPASS で知らせる(__file__ は実在しない)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundle.bundle_dir() == tmp_path
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_bundle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mosaic_tool.bundle'`

- [ ] **Step 3: `mosaic_tool/bundle.py` を実装**

```python
"""リポジトリ直下と PyInstaller 展開先の解決

同梱リソース(アイコン, uv, ワーカー本体)を探すための基準を一元化する。
Qt に依存しないため、GUI を起動しないテストからも使える。
"""
from __future__ import annotations

import sys
from pathlib import Path


def repo_root() -> Path:
    """ソース実行時のリポジトリ直下(このファイルは mosaic_tool/ にある)"""
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """同梱リソースの基準

    PyInstaller は展開先を sys._MEIPASS で知らせる(onefile なら一時ディレクトリ、
    onedir なら _internal / .app の Contents/Frameworks)。パッケージ本体は PYZ に
    取り込まれ __file__ が実在しないため、同梱物の探索には必ずこちらを使う。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return repo_root()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_bundle.py -v`
Expected: PASS（3 件）

- [ ] **Step 5: `resources.py` を `bundle_dir()` 基準へ移行**

`mosaic_tool/resources.py` を以下で全置換する。

```python
"""アプリに同梱するリソースの場所を解決する。"""
from pathlib import Path

from PySide6.QtGui import QIcon

from mosaic_tool.bundle import bundle_dir


def app_icon_path() -> Path:
    # PyInstaller では __file__ が実在しないため、展開先(sys._MEIPASS)を基準にする
    return bundle_dir() / "assets" / "icon.ico"


def load_app_icon() -> QIcon:
    icon_path = app_icon_path()
    if not icon_path.is_file():
        raise FileNotFoundError(f"アプリアイコンが見つかりません: {icon_path}")
    return QIcon(str(icon_path))
```

- [ ] **Step 6: `detect/paths.py` の重複実装を差し替え**

`mosaic_tool/detect/paths.py` の `import` に `from mosaic_tool.bundle import bundle_dir, repo_root` を追加し、`_repo_root()` と `_bundle_dir()` の定義を削除する。ファイル内の `_repo_root()` 呼び出しを `repo_root()` へ、`_bundle_dir()` 呼び出しを `bundle_dir()` へ置き換える（`base_dir()` / `bundled_uv_path()` / `worker_script_source()` の 3 か所）。

- [ ] **Step 7: `.app` でのアイコン解決を検証するテストを追加**

`tests/test_resources.py` の末尾に追記する。

```python
def test_app_icon_is_resolved_from_bundle_dir_when_frozen(monkeypatch, tmp_path):
    # PyInstaller 展開先(.app の Contents/Frameworks 等)を基準に解決すること
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert app_icon_path() == tmp_path / "assets" / "icon.ico"
```

同ファイル冒頭の import に `import sys` を追加する。

- [ ] **Step 8: 全テストを実行**

Run: `python -m pytest tests/test_bundle.py tests/test_resources.py tests/test_detect_paths.py -v`
Expected: PASS（既存の `test_bundled_resources_come_from_meipass_when_frozen` を含め全件）

- [ ] **Step 9: コミット**

```bash
git add mosaic_tool/bundle.py mosaic_tool/resources.py mosaic_tool/detect/paths.py tests/test_bundle.py tests/test_resources.py
git commit -m "refactor(bundle): 同梱リソースの基準ディレクトリ解決を bundle.py へ集約する"
```

---

### Task 2: `detect/paths.py` に OS 分岐を入れる

macOS では `~/Library/Application Support/MosaicTool/` を、Windows では従来どおり実行ファイルの隣をデータ配置基準にする。venv の Python と同梱 uv の名前も OS で分ける。

**Files:**
- Modify: `mosaic_tool/detect/paths.py`
- Test: `tests/test_detect_paths.py`

**Interfaces:**
- Consumes: `mosaic_tool.bundle.bundle_dir` / `repo_root`（Task 1）
- Produces:
  - `paths.user_data_dir() -> Path` — macOS のユーザーデータ配置先
  - `paths.uv_exe_name() -> str` — `"uv.exe"`（Windows）/ `"uv"`（それ以外）
  - `paths.venv_python() -> Path` — 既存関数。OS で分岐するようになる
  - `paths.base_dir() -> Path` — 既存関数。frozen + macOS で `user_data_dir()` を返すようになる
  - 既存の定数 `UV_EXE_NAME` は削除する

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_paths.py` の既存テスト `test_base_dir_is_exe_dir_when_frozen` と `test_runtime_is_ready_when_venv_python_exists` と `test_bundled_resources_come_from_meipass_when_frozen` を以下で置き換え、続く新規テストを追記する。

```python
def test_base_dir_is_exe_dir_when_frozen_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MosaicTool.exe"))
    assert paths.base_dir() == tmp_path


def test_base_dir_is_application_support_when_frozen_on_macos(monkeypatch, tmp_path):
    # .app を /Applications へ移しても書ける場所に置く
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path))
    assert paths.base_dir() == (
        tmp_path / "Library" / "Application Support" / "MosaicTool"
    )


def test_base_dir_is_repo_root_when_not_frozen_on_macos(monkeypatch):
    # ソース実行時は OS を問わずリポジトリ直下
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert (paths.base_dir() / "mosaic_tool").is_dir()


def test_venv_python_is_under_scripts_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.venv_python() == tmp_path / "runtime" / "Scripts" / "python.exe"


def test_venv_python_is_under_bin_on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.venv_python() == tmp_path / "runtime" / "bin" / "python"


def test_runtime_is_ready_when_venv_python_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    python = paths.venv_python()
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert paths.is_runtime_ready()


def test_uv_exe_name_differs_by_os(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.uv_exe_name() == "uv.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.uv_exe_name() == "uv"


def test_bundled_resources_come_from_meipass_when_frozen(monkeypatch, tmp_path):
    # PyInstaller は展開先を sys._MEIPASS で知らせる(__file__ は実在しない)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.bundled_uv_path() == tmp_path / "uv.exe"
    assert (
        paths.worker_script_source()
        == tmp_path / "mosaic_tool" / "detect" / "worker_main.py"
    )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_detect_paths.py -v`
Expected: FAIL — `AttributeError: module 'mosaic_tool.detect.paths' has no attribute 'uv_exe_name'` と、macOS 上での `venv_python` / `base_dir` の不一致

- [ ] **Step 3: `detect/paths.py` を実装**

モジュール冒頭の docstring を書き換え、`UV_EXE_NAME` 定数を削除する。

```python
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
```

`models_dir()` / `runtime_dir()` / `model_files()` / `is_runtime_ready()` / `worker_script_installed()` はそのまま残す。`venv_python()` と `bundled_uv_path()` を以下に差し替える。

```python
def venv_python() -> Path:
    """venv の Python(レイアウトが OS で異なる)"""
    if _is_windows():
        return runtime_dir() / "Scripts" / "python.exe"
    return runtime_dir() / "bin" / "python"


def bundled_uv_path() -> Path:
    return bundle_dir() / uv_exe_name()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_detect_paths.py -v`
Expected: PASS（全件）

- [ ] **Step 5: 既存テストの Windows 決め打ちを解消**

`tests/test_detect_worker_client.py` の `test_worker_command_lists_models_as_arguments` が
`C:/rt/python.exe` などの Windows パスを直書きしている。OS 非依存の形へ書き換える。

```python
def test_worker_command_lists_models_as_arguments(tmp_path):
    python = tmp_path / "runtime" / "python"
    script = tmp_path / "runtime" / "detect_worker.py"
    models = [tmp_path / "models" / "a.pt", tmp_path / "models" / "b.pt"]
    cmd = worker_client.worker_command(python, script, models)
    assert cmd == [str(python), str(script), str(models[0]), str(models[1])]
```

同様に `tests/test_detect_runtime.py` の `UV` / `RUNTIME` 定数も
`Path("C:/app/uv.exe")` から `Path("/app/uv")` / `Path("/app/runtime")` へ書き換える
（コマンド組み立てしか見ないため実在しないパスでよい）。

- [ ] **Step 6: 既存の全テストが壊れていないことを確認**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/detect/paths.py tests/test_detect_paths.py tests/test_detect_worker_client.py tests/test_detect_runtime.py
git commit -m "feat(detect): データ配置と venv のパス解決を OS ごとに分ける"
```

---

### Task 3: `detect/runtime.py` を macOS 対応にする

macOS の torch は PyPI の通常 wheel が MPS 対応済みで CUDA ビルドが存在しないため、CUDA インデックスを付けない。推論デバイスの解決を関数として切り出し、同梱 uv の実行権限とデータ用フォルダの作成も面倒を見る。

**Files:**
- Modify: `mosaic_tool/detect/runtime.py`
- Test: `tests/test_detect_runtime.py`

**Interfaces:**
- Consumes: `paths.bundled_uv_path()` / `paths.runtime_dir()`（Task 2）
- Produces:
  - `runtime.supports_gpu_choice() -> bool` — セットアップで CPU/GPU を選ばせるか
  - `runtime.resolve_device(setting: str) -> str` — 設定値（`"auto"` / `"cpu"`）をワーカーへ渡す device 文字列へ
  - `runtime.ensure_uv_executable(uv: Path) -> None` — POSIX で実行ビットを付け直す

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_runtime.py` の冒頭 import を `import sys` / `from pathlib import Path` / `from mosaic_tool.detect import runtime` に整え、末尾へ追記する。

```python
def test_gpu_install_has_no_cuda_index_on_macos(monkeypatch):
    # macOS に CUDA ビルドは存在しない(通常の wheel が MPS 対応済み)
    monkeypatch.setattr(sys, "platform", "darwin")
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=True)
    assert "--extra-index-url" not in cmd


def test_gpu_choice_is_hidden_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert not runtime.supports_gpu_choice()
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime.supports_gpu_choice()


def test_resolve_device_uses_mps_for_auto_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert runtime.resolve_device("auto") == "mps"
    assert runtime.resolve_device("cpu") == "cpu"


def test_resolve_device_delegates_to_ultralytics_on_windows(monkeypatch):
    # Windows では空文字を渡して ultralytics の自動選択に任せる
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime.resolve_device("auto") == ""
    assert runtime.resolve_device("cpu") == "cpu"


def test_ensure_uv_executable_adds_exec_bit_on_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    uv = tmp_path / "uv"
    uv.write_bytes(b"")
    uv.chmod(0o644)
    runtime.ensure_uv_executable(uv)
    assert uv.stat().st_mode & 0o111


def test_ensure_uv_executable_is_noop_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"")
    # 存在しないファイルでも例外を出さないこと(Windows では何もしない)
    runtime.ensure_uv_executable(tmp_path / "missing.exe")
    runtime.ensure_uv_executable(uv)
```

既存の `test_gpu_install_adds_cuda_index` は Windows を明示するよう先頭に
`monkeypatch.setattr(sys, "platform", "win32")` を追加し、引数に `monkeypatch` を足す。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_detect_runtime.py -v`
Expected: FAIL — `AttributeError: module 'mosaic_tool.detect.runtime' has no attribute 'supports_gpu_choice'`

- [ ] **Step 3: `detect/runtime.py` を実装**

`import sys` を追加し、`install_command()` を差し替えて 3 つの関数を追加する。

```python
def install_command(uv: Path, runtime: Path, use_gpu: bool) -> list[str]:
    """runtime/ の venv へ推論パッケージを入れるコマンド

    GPU 版は torch の配布元が PyPI ではないため、追加のインデックスを指定する。
    macOS には CUDA ビルドが無く、通常の wheel が MPS に対応しているため付けない。
    """
    cmd = [str(uv), "pip", "install", "--python", str(runtime), *PACKAGES]
    if use_gpu and sys.platform != "darwin":
        cmd += ["--extra-index-url", TORCH_CUDA_INDEX_URL]
    return cmd


def supports_gpu_choice() -> bool:
    """セットアップで CPU/GPU を選ばせるか(macOS は構成が 1 通りしかない)"""
    return sys.platform != "darwin"


def resolve_device(setting: str) -> str:
    """設定値をワーカーへ渡す device 文字列へ解決する

    macOS は ultralytics の自動選択が MPS を選ばないため明示する。
    Windows は空文字を渡して自動選択に任せる。
    """
    if setting == "cpu":
        return "cpu"
    return "mps" if sys.platform == "darwin" else ""


def ensure_uv_executable(uv: Path) -> None:
    """同梱した uv に実行ビットを付け直す

    PyInstaller の --add-data はパーミッションを保持しないため、POSIX では
    そのままでは起動できない。
    """
    if sys.platform == "win32":
        return
    uv.chmod(uv.stat().st_mode | 0o111)
```

- [ ] **Step 4: `RuntimeInstaller.start()` に実行権限とフォルダ作成を追加**

`start()` の本体を以下へ差し替える。

```python
    def start(self, use_gpu: bool) -> None:
        uv = paths.bundled_uv_path()
        if not uv.is_file():
            self.finished.emit(False, f"uv が見つかりません: {uv}")
            return
        runtime_dir = paths.runtime_dir()
        try:
            ensure_uv_executable(uv)
            # macOS では Application Support 配下がまだ存在しないことがある
            runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.finished.emit(False, f"推論環境の準備に失敗しました: {e}")
            return
        self._cancelled = False
        self._steps = [
            venv_command(uv, runtime_dir),
            install_command(uv, runtime_dir, use_gpu),
        ]
        self._run_next()
```

- [ ] **Step 5: テストが通ることを確認**

Run: `python -m pytest tests/test_detect_runtime.py -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/detect/runtime.py tests/test_detect_runtime.py
git commit -m "feat(detect): macOS の推論環境と MPS デバイス解決に対応する"
```

---

### Task 4: セットアップ画面と検出要求を macOS 対応にする

macOS では CPU/GPU の選択肢を出さず、推論デバイスは `resolve_device()` で決める。

**Files:**
- Modify: `mosaic_tool/detect/setup_dialog.py`
- Modify: `mosaic_tool/app.py`
- Test: `tests/test_detect_setup_dialog.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `runtime.supports_gpu_choice()` / `runtime.resolve_device()`（Task 3）
- Produces: なし（内部の挙動変更のみ）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_detect_setup_dialog.py` の末尾に追記する（同ファイルの `qapp` フィクスチャと
`setup_dialog` の import は既にある）。

```python
def test_macos_hides_gpu_choice(monkeypatch, qapp):
    # macOS はインストール内容が 1 通りしかないため選択肢を出さない
    monkeypatch.setattr(setup_dialog.runtime, "supports_gpu_choice", lambda: False)
    dialog = setup_dialog.RuntimeSetupDialog()
    assert dialog._gpu_radio is None
    assert dialog._cpu_radio is None


def test_macos_start_requests_cpu_install(monkeypatch, qapp):
    monkeypatch.setattr(setup_dialog.runtime, "supports_gpu_choice", lambda: False)
    dialog = setup_dialog.RuntimeSetupDialog()
    called = []
    monkeypatch.setattr(dialog._installer, "start", lambda use_gpu: called.append(use_gpu))
    dialog._start()
    assert called == [False]
```

`tests/test_app.py` の `test_start_detect_sends_the_models_to_the_worker` の直後へ
1 件追記する（同ファイルの `window` フィクスチャをそのまま使う）。

```python
def test_request_detect_uses_resolved_device(window, monkeypatch):
    """設定値そのままではなく、OS ごとに解決した device をワーカーへ渡すこと"""
    monkeypatch.setattr("mosaic_tool.app.resolve_device", lambda setting: "mps")
    sent = []
    monkeypatch.setattr(
        window._worker, "request", lambda image, models, device: sent.append(device)
    )
    window._request_detect({"a.pt": {"conf": 0.25, "classes": ["face"]}})
    assert sent == ["mps"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_detect_setup_dialog.py tests/test_app.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'runtime'` / `_gpu_radio` が `None` でない

- [ ] **Step 3: `setup_dialog.py` を実装**

import を `from mosaic_tool.detect import downloader, paths, runtime` と
`from mosaic_tool.detect.runtime import RuntimeInstaller, has_nvidia_gpu` にする
（テストが `setup_dialog.runtime` を monkeypatch できるようモジュールごと import する）。

`__init__` のラジオ生成部分を差し替える。

```python
        # macOS はインストール内容が 1 通りしかないため選択肢を出さない
        self._gpu_radio: QRadioButton | None = None
        self._cpu_radio: QRadioButton | None = None
        if runtime.supports_gpu_choice():
            gpu_label = GPU_LABEL + (GPU_DETECTED_NOTE if has_nvidia_gpu() else "")
            self._gpu_radio = QRadioButton(gpu_label)
            self._cpu_radio = QRadioButton(CPU_LABEL)
            # 既定は常に CPU。GPU は容量が大きいため明示的に選んでもらう
            self._cpu_radio.setChecked(True)
            layout.addWidget(self._gpu_radio)
            layout.addWidget(self._cpu_radio)
```

`_start()` と `_set_inputs_enabled()` を差し替える。

```python
    def _start(self) -> None:
        self._running = True
        self._set_inputs_enabled(False)
        use_gpu = self._gpu_radio is not None and self._gpu_radio.isChecked()
        self._installer.start(use_gpu=use_gpu)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for radio in (self._gpu_radio, self._cpu_radio):
            if radio is not None:
                radio.setEnabled(enabled)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)
```

- [ ] **Step 4: `app.py` を実装**

`from mosaic_tool.detect.runtime import resolve_device` を import へ追加し、`_request_detect()` を差し替える。

```python
    def _request_detect(self, models: dict) -> None:
        """表示中の画像の検出をワーカーへ依頼する"""
        self._worker.request(
            str(self._images[self._index]),
            models,
            resolve_device(self._settings.device()),
        )
```

`rg -n "_settings.device\(\)" mosaic_tool` を実行し、他に直接使っている箇所が無いことを確認する（設定画面での読み書きは対象外）。

- [ ] **Step 5: テストが通ることを確認**

Run: `python -m pytest tests/test_detect_setup_dialog.py tests/test_app.py -v`
Expected: PASS（全件）

- [ ] **Step 6: 全テストを実行**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add mosaic_tool/detect/setup_dialog.py mosaic_tool/app.py tests/test_detect_setup_dialog.py tests/test_app.py
git commit -m "feat(detect): macOS ではセットアップの CPU/GPU 選択を出さない"
```

---

### Task 5: `.icns` アイコンを生成する

macOS の `.app` はバンドルアイコンに `.icns` を要求する。マスターは既存の `assets/icon.png`。Pillow の ICNS 書き出しは純 Python 実装のため、Windows 上でも生成・テストできる。

**Files:**
- Modify: `scripts/icon_assets.py`
- Modify: `tests/test_icon_assets.py`
- Create: `assets/icon.icns`（生成物。リポジトリにコミットする）

**Interfaces:**
- Consumes: なし
- Produces:
  - `icon_assets.build_icon_assets(source: Path, ico_output: Path, icns_output: Path) -> None`
  - `icon_assets.icns_sizes(path: Path) -> set[tuple[int, int, int]]` — `(幅, 高さ, 倍率)` の集合
  - `icon_assets.ICNS_REQUIRED_SIZES: frozenset[tuple[int, int, int]]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_icon_assets.py` を以下で全置換する。

```python
from pathlib import Path

from PIL import Image

from icon_assets import (
    ICNS_REQUIRED_SIZES,
    ICON_SIZES,
    build_icon_assets,
    icns_sizes,
    ico_sizes,
)


def _master(tmp_path: Path, size: tuple[int, int] = (512, 512)) -> Path:
    source = tmp_path / "master.png"
    Image.new("RGBA", size, (10, 40, 44, 255)).save(source, "PNG")
    return source


def test_build_icon_assets_creates_all_ico_sizes(tmp_path: Path):
    ico_output = tmp_path / "icon.ico"

    build_icon_assets(_master(tmp_path), ico_output, tmp_path / "icon.icns")

    assert ico_sizes(ico_output) == {(size, size) for size in ICON_SIZES}


def test_build_icon_assets_creates_icns_with_required_sizes(tmp_path: Path):
    icns_output = tmp_path / "icon.icns"

    build_icon_assets(_master(tmp_path), tmp_path / "icon.ico", icns_output)

    assert icns_output.read_bytes()[:4] == b"icns"
    assert ICNS_REQUIRED_SIZES <= icns_sizes(icns_output)


def test_build_icon_assets_rejects_non_square_png(tmp_path: Path):
    source = _master(tmp_path, (512, 256))

    try:
        build_icon_assets(source, tmp_path / "icon.ico", tmp_path / "icon.icns")
    except ValueError as exc:
        assert str(exc) == "PNGマスターは正方形である必要があります"
    else:
        raise AssertionError("非正方形のPNGが受理されました")


def test_repository_icns_is_up_to_date():
    """コミット済みの assets/icon.icns が macOS ビルドで使える形式であること"""
    assets = Path(__file__).resolve().parent.parent / "assets"
    icns = assets / "icon.icns"
    assert icns.is_file()
    assert ICNS_REQUIRED_SIZES <= icns_sizes(icns)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_icon_assets.py -v`
Expected: FAIL — `ImportError: cannot import name 'ICNS_REQUIRED_SIZES' from 'icon_assets'`

- [ ] **Step 3: `scripts/icon_assets.py` を実装**

以下で全置換する。

```python
"""MosaicToolの配布用アイコン資産を生成する。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
# .icns に必ず含まれていてほしい (幅, 高さ, 倍率)。Pillow は 1024 まで書き出すが、
# 読み戻したときの表現は Pillow のバージョンで変わりうるため下限だけを固定する
ICNS_REQUIRED_SIZES = frozenset(
    {(512, 512, 1), (256, 256, 1), (128, 128, 1), (512, 512, 2), (256, 256, 2)}
)


def build_icon_assets(source: Path, ico_output: Path, icns_output: Path) -> None:
    with Image.open(source) as image:
        if image.width != image.height:
            raise ValueError("PNGマスターは正方形である必要があります")
        master = image.convert("RGBA")

    master.save(
        ico_output,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )
    # Pillow の ICNS 書き出しは純 Python 実装のため、macOS 以外でも生成できる
    master.save(icns_output, format="ICNS")


def ico_sizes(path: Path) -> set[tuple[int, int]]:
    with Image.open(path) as image:
        return set(image.ico.sizes())


def icns_sizes(path: Path) -> set[tuple[int, int, int]]:
    with Image.open(path) as image:
        return {tuple(size) for size in image.info["sizes"]}


if __name__ == "__main__":
    # scripts/ に置くが、資産の入出力はリポジトリ直下の assets/ を基準にする
    assets = Path(__file__).resolve().parent.parent / "assets"
    build_icon_assets(assets / "icon.png", assets / "icon.ico", assets / "icon.icns")
```

- [ ] **Step 4: アイコン資産を再生成**

Run: `python scripts/icon_assets.py`
Expected: `assets/icon.icns` が生成される（`assets/icon.ico` は内容が変わらない）

- [ ] **Step 5: テストが通ることを確認**

Run: `python -m pytest tests/test_icon_assets.py -v`
Expected: PASS（4 件）

- [ ] **Step 6: コミット**

```bash
git add scripts/icon_assets.py tests/test_icon_assets.py assets/icon.icns
git commit -m "feat(assets): macOS バンドル用の icon.icns を生成する"
```

---

### Task 6: リリーススクリプト（bump / tag）を Python へ移植する

`bump.ps1` / `tag.ps1` を Python へ移植し、共通処理を `scripts/appinfo.py` に切り出す。オプションと検証の内容は PowerShell 版と同じにする。

**Files:**
- Create: `scripts/appinfo.py`, `scripts/bump.py`, `scripts/tag.py`
- Create: `tests/test_appinfo.py`, `tests/test_bump.py`
- Delete: `scripts/bump.ps1`, `scripts/tag.ps1`
- Modify: `justfile`

**Interfaces:**
- Consumes: なし
- Produces:
  - `appinfo.repo_root() -> Path`
  - `appinfo.read_app_name() -> str` / `appinfo.read_version() -> str`
  - `appinfo.write_version(target: str) -> None`
  - `appinfo.next_version(current: str, spec: str) -> str` — `spec` は `patch` / `minor` / `major` / `x.y.z`
  - `appinfo.run(args: list[str], **kwargs) -> subprocess.CompletedProcess` — 失敗時に日本語メッセージで `SystemExit`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_appinfo.py` を新規作成する。

```python
"""version.py の読み書きとバージョン解決の検証"""
import pytest

import appinfo

SAMPLE = '"""説明"""\n\nAPP_NAME = "MosaicTool"\n__version__ = "1.2.3"\n'


def test_read_app_name_and_version(monkeypatch, tmp_path):
    path = tmp_path / "version.py"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(appinfo, "version_path", lambda: path)
    assert appinfo.read_app_name() == "MosaicTool"
    assert appinfo.read_version() == "1.2.3"


def test_read_version_accepts_crlf(monkeypatch, tmp_path):
    # CRLF のファイルでも行末に引きずられず読めること
    path = tmp_path / "version.py"
    path.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setattr(appinfo, "version_path", lambda: path)
    assert appinfo.read_version() == "1.2.3"


def test_read_version_rejects_missing_definition(monkeypatch, tmp_path):
    path = tmp_path / "version.py"
    path.write_text("APP_NAME = \"X\"\n", encoding="utf-8")
    monkeypatch.setattr(appinfo, "version_path", lambda: path)
    with pytest.raises(SystemExit):
        appinfo.read_version()


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("patch", "1.2.4"),
        ("minor", "1.3.0"),
        ("major", "2.0.0"),
        ("PATCH", "1.2.4"),
        ("2.5.0", "2.5.0"),
    ],
)
def test_next_version(spec, expected):
    assert appinfo.next_version("1.2.3", spec) == expected


@pytest.mark.parametrize("spec", ["1.2", "v1.2.3", "latest", "1.2.3.4"])
def test_next_version_rejects_bad_spec(spec):
    with pytest.raises(SystemExit):
        appinfo.next_version("1.2.3", spec)


def test_write_version_keeps_other_lines(monkeypatch, tmp_path):
    path = tmp_path / "version.py"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(appinfo, "version_path", lambda: path)
    appinfo.write_version("1.3.0")
    text = path.read_text(encoding="utf-8")
    assert '__version__ = "1.3.0"' in text
    assert 'APP_NAME = "MosaicTool"' in text
    assert text.startswith('"""説明"""')


def test_write_version_keeps_crlf(monkeypatch, tmp_path):
    # Windows でチェックアウトしたファイルの改行コードを壊さないこと
    path = tmp_path / "version.py"
    path.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setattr(appinfo, "version_path", lambda: path)
    appinfo.write_version("1.3.0")
    assert b'__version__ = "1.3.0"\r\n' in path.read_bytes()
```

`tests/test_bump.py` を新規作成する。

```python
"""bump の対象バージョン決定の検証(git 操作は行わない)"""
import pytest

import appinfo
import bump


def test_resolve_target_bumps_patch(monkeypatch):
    monkeypatch.setattr(appinfo, "read_version", lambda: "1.2.3")
    assert bump.resolve_target("patch") == ("1.2.3", "1.2.4")


def test_resolve_target_rejects_same_version(monkeypatch):
    monkeypatch.setattr(appinfo, "read_version", lambda: "1.2.3")
    with pytest.raises(SystemExit):
        bump.resolve_target("1.2.3")


def test_resolve_target_rejects_malformed_current(monkeypatch):
    monkeypatch.setattr(appinfo, "read_version", lambda: "1.2")
    with pytest.raises(SystemExit):
        bump.resolve_target("patch")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_appinfo.py tests/test_bump.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'appinfo'`

- [ ] **Step 3: `scripts/appinfo.py` を実装**

```python
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
```

- [ ] **Step 4: `scripts/bump.py` を実装**

```python
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
```

- [ ] **Step 5: `scripts/tag.py` を実装**

```python
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
```

- [ ] **Step 6: テストが通ることを確認**

Run: `python -m pytest tests/test_appinfo.py tests/test_bump.py -v`
Expected: PASS（全件）

- [ ] **Step 7: dry-run で実挙動を確認**

Run: `python scripts/bump.py patch --dry-run && python scripts/tag.py --dry-run --branch $(git rev-parse --abbrev-ref HEAD)`
Expected: どちらも `== dry-run: 検証のみ実行しました ==` を出して終了する

- [ ] **Step 8: `.ps1` を削除して `justfile` を更新**

`scripts/bump.ps1` と `scripts/tag.ps1` を削除し、`justfile` の該当レシピを差し替える。

```make
# mosaic_tool/version.py のバージョンを更新してコミットする (例: just bump patch / just bump 1.1.0 / just bump patch --dry-run)
bump VERSION="patch" *ARGS:
    python scripts/bump.py {{VERSION}} {{ARGS}}

# mosaic_tool/version.py のバージョンでタグを作成して push する (例: just tag / just tag --dry-run)
tag *ARGS:
    python scripts/tag.py {{ARGS}}
```

- [ ] **Step 9: コミット**

```bash
git add scripts/appinfo.py scripts/bump.py scripts/tag.py tests/test_appinfo.py tests/test_bump.py justfile
git rm scripts/bump.ps1 scripts/tag.ps1
git commit -m "refactor(scripts): bump / tag を Python へ移植する"
```

---

### Task 7: ビルドスクリプトを Python へ移植して macOS に対応する

`build.ps1` を `scripts/build.py` へ移植する。uv の取得先・アイコン・出力形式を OS で分ける。macOS では `--onedir --windowed` で `.app` を作る。

**Files:**
- Create: `scripts/build.py`, `tests/test_build_script.py`
- Delete: `scripts/build.ps1`
- Modify: `justfile`

**Interfaces:**
- Consumes: `appinfo.*`（Task 6）、`assets/icon.icns`（Task 5）、`paths.uv_exe_name()` と同じ命名規則
- Produces:
  - `build.uv_asset_name() -> str` — ダウンロードする uv のアセット名
  - `build.uv_exe_name() -> str` — 同梱する uv の実行ファイル名
  - `build.icon_path() -> Path` — PyInstaller へ渡すアイコン
  - `build.pyinstaller_args(app_name: str, one_dir: bool) -> list[str]`
  - `build.built_app_path(app_name: str, one_dir: bool) -> Path` — 成果物のパス
  - `build.main()` — CLI エントリポイント

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_build_script.py` を新規作成する。

```python
"""ビルドスクリプトの OS 分岐の検証(PyInstaller は実行しない)"""
import sys

import pytest

import build


def test_uv_asset_is_windows_zip(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert build.uv_asset_name() == "uv-x86_64-pc-windows-msvc.zip"
    assert build.uv_exe_name() == "uv.exe"


def test_uv_asset_is_apple_silicon_tarball(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert build.uv_asset_name() == "uv-aarch64-apple-darwin.tar.gz"
    assert build.uv_exe_name() == "uv"


def test_uv_asset_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit):
        build.uv_asset_name()


def test_icon_is_icns_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert build.icon_path().name == "icon.icns"
    monkeypatch.setattr(sys, "platform", "win32")
    assert build.icon_path().name == "icon.ico"


def test_macos_build_is_always_onedir(monkeypatch):
    # onefile は毎回一時展開するため公証との相性が悪い
    monkeypatch.setattr(sys, "platform", "darwin")
    args = build.pyinstaller_args("MosaicTool", one_dir=False)
    assert "--onedir" in args
    assert "--onefile" not in args


def test_windows_build_defaults_to_onefile(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    args = build.pyinstaller_args("MosaicTool", one_dir=False)
    assert "--onefile" in args


def test_pyinstaller_bundles_uv_icon_and_worker(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    joined = " ".join(build.pyinstaller_args("MosaicTool", one_dir=True))
    assert "icon.ico" in joined          # QIcon 用に .ico も同梱する
    assert "worker_main.py" in joined
    assert build.uv_exe_name() in joined
    assert "--windowed" in joined


def test_built_app_path_is_app_bundle_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert build.built_app_path("MosaicTool", one_dir=True).name == "MosaicTool.app"


def test_built_app_path_is_exe_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert build.built_app_path("MosaicTool", one_dir=False).name == "MosaicTool.exe"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_build_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build'`

- [ ] **Step 3: `scripts/build.py` を実装**

```python
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
    print(f"== 完了: {output} ==")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_build_script.py -v`
Expected: PASS（全件）

- [ ] **Step 5: 実機でビルドして起動を確認**

Run: `python scripts/build.py --clean`
Expected: `dist/MosaicTool.app` が生成される

Run: `open dist/MosaicTool.app`
Expected: ウィンドウが開き、Dock のアイコンが MosaicTool のものになっている

- [ ] **Step 6: `.ps1` を削除して `justfile` を更新**

`scripts/build.ps1` を削除し、`justfile` の `build` レシピを差し替える。

```make
# 実行ファイルをローカルでビルドする (例: just build --clean)
build *ARGS:
    python scripts/build.py {{ARGS}}
```

- [ ] **Step 7: コミット**

```bash
git add scripts/build.py tests/test_build_script.py justfile
git rm scripts/build.ps1
git commit -m "feat(build): ビルドを Python へ移植し macOS の .app を作れるようにする"
```

---

### Task 8: パッケージングを Python へ移植して macOS の zip を作る

`package.ps1` を `scripts/package.py` へ移植する。macOS では `.app` の実行ビットとシンボリックリンクが壊れないよう `ditto` で zip 化する。

**Files:**
- Create: `scripts/package.py`, `tests/test_package_script.py`
- Delete: `scripts/package.ps1`
- Modify: `justfile`

**Interfaces:**
- Consumes: `build.built_app_path()` / `build.main()`（Task 7）、`appinfo.*`（Task 6）
- Produces:
  - `package.platform_tag() -> str` — `"win-x64"` / `"mac-arm64"`
  - `package.package_name(app_name: str, version: str) -> str`
  - `package.stage(app: Path, stage_dir: Path) -> None` — 配布物を組み立てる
  - `package.make_zip(stage_root: Path, stage_dir: Path, zip_path: Path) -> None`
  - `package.emit_github_output(name: str, value: str) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_package_script.py` を新規作成する。

```python
"""配布物の命名とステージングの検証(ビルドは行わない)"""
import sys
import zipfile

import pytest

import package


def test_platform_tag_by_os(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert package.platform_tag() == "win-x64"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert package.platform_tag() == "mac-arm64"


def test_platform_tag_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit):
        package.platform_tag()


def test_package_name_includes_version_and_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert package.package_name("MosaicTool", "1.2.3") == "MosaicTool-v1.2.3-mac-arm64"
    monkeypatch.setattr(sys, "platform", "win32")
    assert package.package_name("MosaicTool", "1.2.3") == "MosaicTool-v1.2.3-win-x64"


def test_emit_github_output_appends_utf8(monkeypatch, tmp_path):
    output = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    package.emit_github_output("package_name", "MosaicTool-v1.2.3-mac-arm64")
    assert output.read_text(encoding="utf-8") == (
        "package_name=MosaicTool-v1.2.3-mac-arm64\n"
    )


def test_emit_github_output_is_noop_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    package.emit_github_output("package_name", "x")  # 例外を出さないこと


def test_make_zip_uses_forward_slashes(monkeypatch, tmp_path):
    """他 OS や 7-Zip でも展開できるよう、エントリ名は / 区切りにする"""
    monkeypatch.setattr(sys, "platform", "win32")
    stage_root = tmp_path / "package"
    stage_dir = stage_root / "MosaicTool-v1.2.3-win-x64"
    (stage_dir / "sub").mkdir(parents=True)
    (stage_dir / "MosaicTool.exe").write_bytes(b"exe")
    (stage_dir / "sub" / "README.md").write_text("readme", encoding="utf-8")
    zip_path = tmp_path / "out.zip"

    package.make_zip(stage_root, stage_dir, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
    assert names == [
        "MosaicTool-v1.2.3-win-x64/MosaicTool.exe",
        "MosaicTool-v1.2.3-win-x64/sub/README.md",
    ]


def test_stage_copies_app_and_readme(monkeypatch, tmp_path):
    app = tmp_path / "MosaicTool.exe"
    app.write_bytes(b"exe")
    readme = tmp_path / "README.md"
    readme.write_text("readme", encoding="utf-8")
    monkeypatch.setattr(package.appinfo, "repo_root", lambda: tmp_path)
    stage_dir = tmp_path / "stage" / "MosaicTool-v1.2.3-win-x64"

    package.stage(app, stage_dir)

    assert (stage_dir / "MosaicTool.exe").is_file()
    assert (stage_dir / "README.md").is_file()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_package_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'package'`

- [ ] **Step 3: `scripts/package.py` を実装**

```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/test_package_script.py -v`
Expected: PASS（全件）

- [ ] **Step 5: 実機でパッケージして展開・起動を確認**

Run: `python scripts/package.py --clean`
Expected: `dist/MosaicTool-v1.1.0-mac-arm64.zip` が生成される

Run: `rm -rf "$TMPDIR/pkgcheck" && mkdir -p "$TMPDIR/pkgcheck" && ditto -x -k dist/MosaicTool-v*-mac-arm64.zip "$TMPDIR/pkgcheck" && open "$TMPDIR/pkgcheck"/*/MosaicTool.app`
Expected: 展開した `.app` がウィンドウを開く（実行ビットが保たれている）

- [ ] **Step 6: `.ps1` を削除して `justfile` を更新**

`scripts/package.ps1` を削除する。`justfile` から `_ps` 変数と `set windows-shell` の行を削除し、`package` レシピを差し替える。

```make
# 配布用 zip をローカルで作成する (例: just package --clean)
package *ARGS:
    python scripts/package.py {{ARGS}}
```

`just --list` が Windows でも動くよう、シェバングレシピは使わない（全レシピが 1 行の `python ...` 呼び出しであることを確認する）。

- [ ] **Step 7: コミット**

```bash
git add scripts/package.py tests/test_package_script.py justfile
git rm scripts/package.ps1
git commit -m "feat(build): パッケージングを Python へ移植し macOS の zip を作れるようにする"
```

---

### Task 9: Finder からのファイルオープンに対応する

Windows の「exe へのドラッグ&ドロップ」に相当する体験を macOS でも提供する。`Info.plist` に対応画像形式を宣言し、`QFileOpenEvent` を処理する。

**Files:**
- Create: `mosaic_tool/application.py`, `scripts/macos_bundle.py`
- Create: `tests/test_application.py`, `tests/test_macos_bundle.py`
- Modify: `mosaic_tool/__main__.py`, `scripts/build.py`

**Interfaces:**
- Consumes: `io_utils.IMAGE_EXTS`、`MainWindow.open_paths(paths: list[Path])`
- Produces:
  - `mosaic_tool.application.MosaicApplication(argv: list[str])` — `set_window(window)` を持つ `QApplication`
  - `macos_bundle.document_types() -> list[dict]` — `CFBundleDocumentTypes` の内容
  - `macos_bundle.patch_info_plist(app: Path) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_application.py` を新規作成する。

```python
"""Finder / Dock からのファイルオープン (QFileOpenEvent) の検証"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.application import MosaicApplication  # noqa: E402


class _FakeFileOpenEvent:
    """QFileOpenEvent の代役(type() と file() だけ使う)"""

    def __init__(self, path: Path):
        self._path = path

    def type(self):
        return QEvent.Type.FileOpen

    def file(self):
        return str(self._path)


class _FakeWindow:
    def __init__(self):
        self.opened: list[list[Path]] = []

    def open_paths(self, paths):
        self.opened.append(list(paths))


@pytest.fixture
def app():
    existing = QApplication.instance()
    if existing is not None:
        pytest.skip("既に別の QApplication が生成されているため検証できません")
    application = MosaicApplication([])
    yield application
    application.shutdown()


def test_file_open_event_is_forwarded_to_window(app, tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"")
    window = _FakeWindow()
    app.set_window(window)

    app.event(_FakeFileOpenEvent(image))

    assert window.opened == [[image]]


def test_file_open_before_window_is_replayed(app, tmp_path):
    """ウィンドウ生成前に届いたイベントは、生成後にまとめて流す"""
    image = tmp_path / "a.png"
    image.write_bytes(b"")
    app.event(_FakeFileOpenEvent(image))
    window = _FakeWindow()

    app.set_window(window)

    assert window.opened == [[image]]


def test_missing_file_is_ignored(app, tmp_path):
    window = _FakeWindow()
    app.set_window(window)

    app.event(_FakeFileOpenEvent(tmp_path / "missing.png"))

    assert window.opened == []
```

`tests/test_macos_bundle.py` を新規作成する。

```python
"""Info.plist の document types 生成の検証"""
import plistlib

import macos_bundle
from mosaic_tool.io_utils import IMAGE_EXTS


def test_document_types_cover_all_supported_extensions():
    types = macos_bundle.document_types()
    declared = {ext for t in types for ext in t["CFBundleTypeExtensions"]}
    assert declared == {ext.lstrip(".") for ext in IMAGE_EXTS}


def test_patch_info_plist_adds_document_types(tmp_path):
    app = tmp_path / "MosaicTool.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    plist = contents / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleName": "MosaicTool"}))

    macos_bundle.patch_info_plist(app)

    data = plistlib.loads(plist.read_bytes())
    assert data["CFBundleName"] == "MosaicTool"        # 既存のキーは残る
    assert data["CFBundleDocumentTypes"] == macos_bundle.document_types()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_application.py tests/test_macos_bundle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mosaic_tool.application'`

- [ ] **Step 3: `mosaic_tool/application.py` を実装**

```python
"""Finder / Dock から渡されたファイルを受け取る QApplication

macOS ではコマンドライン引数ではなく QFileOpenEvent でパスが届く。
アプリ起動と同時にドロップされた場合はウィンドウより先にイベントが来るため、
いったん貯めておいてウィンドウが用意できた時点で流す。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


class MosaicApplication(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self._window = None
        self._pending: list[Path] = []

    def set_window(self, window) -> None:
        self._window = window
        if self._pending:
            window.open_paths(self._pending)
            self._pending = []

    def shutdown(self) -> None:
        """テストから明示的に後始末するためのフック"""
        self._window = None
        self._pending = []

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            path = Path(event.file())
            if path.exists():
                if self._window is None:
                    self._pending.append(path)
                else:
                    self._window.open_paths([path])
            return True
        return super().event(event)
```

- [ ] **Step 4: `mosaic_tool/__main__.py` を実装**

```python
"""エントリポイント: 実行ファイルへの D&D は引数としてパスが渡される

macOS では引数ではなく QFileOpenEvent で届くため、MosaicApplication が受け取る。
"""
import sys
from pathlib import Path

from mosaic_tool.app import MainWindow
from mosaic_tool.application import MosaicApplication
from mosaic_tool.resources import load_app_icon


def main():
    app = MosaicApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    paths = [a for a in sys.argv[1:] if Path(a).exists()]
    win = MainWindow(paths)
    app.set_window(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: `scripts/macos_bundle.py` を実装**

```python
"""macOS の .app バンドルへの後処理

PyInstaller の CLI では CFBundleDocumentTypes を指定できないため、
生成後の Info.plist へ追記する。署名の前に行うこと(後から書き換えると
署名が壊れる)。
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import appinfo
from mosaic_tool.io_utils import IMAGE_EXTS


def document_types() -> list[dict]:
    """Finder の「このアプリケーションで開く」に出す対応形式"""
    return [
        {
            "CFBundleTypeName": "画像ファイル",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": sorted(
                ext.lstrip(".") for ext in IMAGE_EXTS
            ),
        }
    ]


def patch_info_plist(app: Path) -> None:
    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        appinfo.fail(f"Info.plist が見つかりません: {plist}")
    data = plistlib.loads(plist.read_bytes())
    data["CFBundleDocumentTypes"] = document_types()
    plist.write_bytes(plistlib.dumps(data))
```

`scripts/macos_bundle.py` は `mosaic_tool` を import するため、`sys.path` にリポジトリ直下が入っている必要がある。`pyproject.toml` の `pythonpath = [".", "scripts"]` によりテストからは解決されるが、`build.py` から呼ぶときは `build.py` がリポジトリ直下で実行されるため問題ない。

- [ ] **Step 6: `scripts/build.py` から Info.plist をパッチする**

`build.py` の `main()` の成果物確認の直後に追記する。

```python
    if is_macos():
        # 署名の前に行う(署名後に書き換えると署名が壊れる)
        macos_bundle.patch_info_plist(output)
        print("-- Info.plist に対応ファイル形式を追記しました")
```

冒頭の import に `import macos_bundle` を追加する。

- [ ] **Step 7: テストが通ることを確認**

Run: `python -m pytest tests/test_application.py tests/test_macos_bundle.py -v`
Expected: PASS（全件）

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 8: 実機で Finder 連携を確認**

Run: `python scripts/build.py --clean && open -a "$PWD/dist/MosaicTool.app" assets/icon.png`
Expected: MosaicTool が起動し、`assets/icon.png` が開かれる

- [ ] **Step 9: コミット**

```bash
git add mosaic_tool/application.py mosaic_tool/__main__.py scripts/macos_bundle.py scripts/build.py tests/test_application.py tests/test_macos_bundle.py
git commit -m "feat(macos): Finder から画像を開けるようにする"
```

---

### Task 10: コード署名と公証に対応する

Developer ID で署名し、公証して staple する。Secrets が未設定なら ad-hoc 署名のまま公証をスキップして続行する（証明書取得前でもビルドが通るようにするため）。

**Files:**
- Create: `scripts/macos_sign.py`, `scripts/entitlements.plist`
- Create: `tests/test_macos_sign.py`
- Modify: `scripts/build.py`, `scripts/package.py`

**Interfaces:**
- Consumes: `build.built_app_path()`（Task 7）、`package.stage()`（Task 8）
- Produces:
  - `macos_sign.entitlements_path() -> Path`
  - `macos_sign.macho_targets(app: Path) -> list[Path]` — 署名対象を深い順に並べたもの
  - `macos_sign.codesign_command(identity: str, target: Path) -> list[str]`
  - `macos_sign.sign_app(app: Path, identity: str) -> bool` — 署名したら `True`、ID 未設定なら `False`
  - `macos_sign.notary_credentials() -> dict[str, str] | None`
  - `macos_sign.notarize_and_staple(app: Path) -> bool` — 公証したら `True`、資格情報が無ければ `False`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_macos_sign.py` を新規作成する。

```python
"""署名・公証のコマンド組み立てと資格情報の判定の検証(codesign は実行しない)"""
from pathlib import Path

import macos_sign


def test_entitlements_file_exists():
    path = macos_sign.entitlements_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    # venv の Python を子プロセスとして起動するために必要
    assert "com.apple.security.cs.disable-library-validation" in text
    assert "com.apple.security.cs.allow-jit" in text
    assert "com.apple.security.cs.allow-unsigned-executable-memory" in text


def test_codesign_command_uses_hardened_runtime():
    cmd = macos_sign.codesign_command("Developer ID Application: X (TEAM)", Path("/a/b"))
    assert cmd[0] == "codesign"
    assert "--force" in cmd
    assert "--timestamp" in cmd
    assert cmd[cmd.index("--options") + 1] == "runtime"
    assert cmd[cmd.index("--sign") + 1] == "Developer ID Application: X (TEAM)"
    assert cmd[-1] == "/a/b"


def test_macho_targets_are_deepest_first(tmp_path):
    """入れ子のバイナリを内側から署名する(外側を先に署名すると壊れる)"""
    app = tmp_path / "MosaicTool.app"
    inner = app / "Contents" / "Frameworks" / "sub" / "deep.dylib"
    inner.parent.mkdir(parents=True)
    inner.write_bytes(b"")
    shallow = app / "Contents" / "Frameworks" / "lib.so"
    shallow.write_bytes(b"")
    exe = app / "Contents" / "MacOS" / "MosaicTool"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    targets = macos_sign.macho_targets(app)

    assert targets[-1] == app          # バンドル本体は最後
    assert targets.index(inner) < targets.index(shallow)
    assert exe in targets


def test_notary_credentials_requires_all_values(monkeypatch):
    for key in ("MACOS_NOTARY_APPLE_ID", "MACOS_NOTARY_PASSWORD", "MACOS_TEAM_ID"):
        monkeypatch.delenv(key, raising=False)
    assert macos_sign.notary_credentials() is None

    monkeypatch.setenv("MACOS_NOTARY_APPLE_ID", "a@example.com")
    monkeypatch.setenv("MACOS_NOTARY_PASSWORD", "pw")
    assert macos_sign.notary_credentials() is None  # TEAM_ID が欠けている

    monkeypatch.setenv("MACOS_TEAM_ID", "TEAM")
    assert macos_sign.notary_credentials() == {
        "apple_id": "a@example.com", "password": "pw", "team_id": "TEAM",
    }


def test_sign_app_is_skipped_without_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("MACOS_SIGN_IDENTITY", raising=False)
    assert macos_sign.sign_app(tmp_path / "MosaicTool.app", "") is False


def test_notarize_is_skipped_without_credentials(monkeypatch, tmp_path):
    for key in ("MACOS_NOTARY_APPLE_ID", "MACOS_NOTARY_PASSWORD", "MACOS_TEAM_ID"):
        monkeypatch.delenv(key, raising=False)
    assert macos_sign.notarize_and_staple(tmp_path / "MosaicTool.app") is False
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/test_macos_sign.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'macos_sign'`

- [ ] **Step 3: `scripts/entitlements.plist` を作成**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- 自動検出は runtime/ の venv の Python を子プロセスとして起動する。
         Hardened Runtime のまま torch/numpy を読み込めるようにする -->
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
```

- [ ] **Step 4: `scripts/macos_sign.py` を実装**

```python
"""macOS の署名・公証

Secrets が未設定でもビルドを通したいので、資格情報が無ければ黙って
スキップする(PyInstaller が付ける ad-hoc 署名のまま配る)。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import appinfo

# 署名対象とみなす拡張子(拡張子なしの実行ファイルは Contents/MacOS で拾う)
_BINARY_SUFFIXES = {".dylib", ".so"}


def entitlements_path() -> Path:
    return Path(__file__).resolve().parent / "entitlements.plist"


def macho_targets(app: Path) -> list[Path]:
    """署名対象を内側から順に並べる(外側を先に署名すると壊れる)"""
    targets = [
        p for p in app.rglob("*")
        if p.is_file() and not p.is_symlink()
        and (p.suffix in _BINARY_SUFFIXES or p.parent.name == "MacOS")
    ]
    # パスの深い順 → 同じ深さは名前順で安定させる
    targets.sort(key=lambda p: (-len(p.parts), str(p)))
    return [*targets, app]


def codesign_command(identity: str, target: Path) -> list[str]:
    return [
        "codesign",
        "--force",
        "--timestamp",
        "--options", "runtime",
        "--entitlements", str(entitlements_path()),
        "--sign", identity,
        str(target),
    ]


def sign_app(app: Path, identity: str = "") -> bool:
    """Developer ID で署名する。ID が無ければ何もせず False を返す"""
    identity = identity or os.environ.get("MACOS_SIGN_IDENTITY", "")
    if not identity:
        print("-- 署名 ID が未設定のため ad-hoc 署名のままにします")
        return False
    for target in macho_targets(app):
        appinfo.run(codesign_command(identity, target))
    appinfo.run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    print(f"== 署名しました: {app} ==")
    return True


def notary_credentials() -> dict[str, str] | None:
    """公証に必要な 3 つが揃っているときだけ返す"""
    apple_id = os.environ.get("MACOS_NOTARY_APPLE_ID", "")
    password = os.environ.get("MACOS_NOTARY_PASSWORD", "")
    team_id = os.environ.get("MACOS_TEAM_ID", "")
    if not (apple_id and password and team_id):
        return None
    return {"apple_id": apple_id, "password": password, "team_id": team_id}


def notarize_and_staple(app: Path) -> bool:
    """公証して staple する。資格情報が無ければ何もせず False を返す"""
    credentials = notary_credentials()
    if credentials is None:
        print("-- 公証の資格情報が未設定のためスキップします")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "notarize.zip"
        appinfo.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
        appinfo.run([
            "xcrun", "notarytool", "submit", str(archive),
            "--apple-id", credentials["apple_id"],
            "--team-id", credentials["team_id"],
            "--password", credentials["password"],
            "--wait",
        ])
    # staple はチケットを .app に埋め込む(オフラインでも Gatekeeper を通る)
    appinfo.run(["xcrun", "stapler", "staple", str(app)])
    print(f"== 公証しました: {app} ==")
    return True
```

- [ ] **Step 5: `build.py` と `package.py` から呼び出す**

`scripts/build.py` の import に `import macos_sign` を追加し、Info.plist パッチの直後に追記する。

```python
        macos_sign.sign_app(output, args.sign_identity)
```

`scripts/package.py` の import に `import macos_sign` を追加し、`stage()` を呼ぶ直前に追記する。

```python
    if sys.platform == "darwin":
        # 公証と staple は zip に固める前に .app へ適用する
        macos_sign.notarize_and_staple(app)
```

- [ ] **Step 6: テストが通ることを確認**

Run: `python -m pytest tests/test_macos_sign.py -v`
Expected: PASS（全件）

- [ ] **Step 7: 資格情報なしでパッケージが通ることを確認**

Run: `env -u MACOS_SIGN_IDENTITY -u MACOS_NOTARY_APPLE_ID -u MACOS_NOTARY_PASSWORD -u MACOS_TEAM_ID python scripts/package.py --clean`
Expected: 「署名 ID が未設定」「公証の資格情報が未設定」を出したうえで zip が生成される

- [ ] **Step 8: コミット**

```bash
git add scripts/macos_sign.py scripts/entitlements.plist scripts/build.py scripts/package.py tests/test_macos_sign.py
git commit -m "feat(macos): Developer ID 署名と公証に対応する(未設定時は ad-hoc)"
```

---

### Task 11: リリースワークフローを 2 プラットフォーム構成にする

Release 作成を独立したジョブへ分離し、Windows と macOS のビルドを並べる。バージョン整合の検証は前段の `meta` ジョブに置き、不一致なら 2 つのビルドを始める前に落とす。

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `scripts/package.py` の `package_name` 出力（Task 8）、署名 Secrets（Task 10）
- Produces: なし

- [ ] **Step 1: `.github/workflows/release.yml` を全置換**

```yaml
# 配布用 zip をビルドして GitHub Releases へアップロードするワークフロー
#
# - タグ push (v*) : zip をビルドし、そのタグの Release を作成してアップロードする
# - 手動実行       : zip をビルドし、Actions の成果物 (Artifact) としてのみ残す
name: release

on:
  push:
    tags:
      - "v*"
  workflow_dispatch:

permissions:
  contents: write # Release の作成とアセットのアップロードに必要

jobs:
  # タグとの整合を先に検証し、不一致なら 2 つのビルドを始める前に落とす
  meta:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    outputs:
      app_name: ${{ steps.read.outputs.app_name }}
      app_version: ${{ steps.read.outputs.app_version }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: バージョン情報を取得
        id: read
        run: |
          python - <<'PY' >> "$GITHUB_OUTPUT"
          import os, sys
          sys.path.insert(0, "scripts")
          import appinfo
          name, version = appinfo.read_app_name(), appinfo.read_version()
          ref = os.environ.get("GITHUB_REF", "")
          # タグ push の場合は mosaic_tool/version.py と食い違っていないか検証する
          if ref.startswith("refs/tags/"):
              tag = ref.removeprefix("refs/tags/")
              if tag != f"v{version}":
                  raise SystemExit(
                      f"タグ ({tag}) と mosaic_tool/version.py のバージョン (v{version}) が一致しません"
                  )
          print(f"app_name={name}")
          print(f"app_version={version}")
          PY

  build:
    needs: meta
    timeout-minutes: 45
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements.txt

      # Secrets が未設定なら import せず、ad-hoc 署名のままビルドする
      # (secrets コンテキストは step の if からは参照できないためシェル側で判定する)
      - name: 署名証明書を読み込む
        if: runner.os == 'macOS'
        env:
          MACOS_CERTIFICATE: ${{ secrets.MACOS_CERTIFICATE }}
          MACOS_CERTIFICATE_PWD: ${{ secrets.MACOS_CERTIFICATE_PWD }}
        run: |
          if [ -z "$MACOS_CERTIFICATE" ]; then
            echo "-- 署名証明書が未設定のためスキップします"
            exit 0
          fi
          KEYCHAIN="$RUNNER_TEMP/build.keychain-db"
          KEYCHAIN_PWD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
          echo "$MACOS_CERTIFICATE" | base64 --decode > "$RUNNER_TEMP/cert.p12"
          security create-keychain -p "$KEYCHAIN_PWD" "$KEYCHAIN"
          security set-keychain-settings -lut 21600 "$KEYCHAIN"
          security unlock-keychain -p "$KEYCHAIN_PWD" "$KEYCHAIN"
          security import "$RUNNER_TEMP/cert.p12" -k "$KEYCHAIN" \
            -P "$MACOS_CERTIFICATE_PWD" -T /usr/bin/codesign
          security set-key-partition-list -S apple-tool:,apple:,codesign: \
            -s -k "$KEYCHAIN_PWD" "$KEYCHAIN"
          security list-keychain -d user -s "$KEYCHAIN" login.keychain-db
          rm -f "$RUNNER_TEMP/cert.p12"

      # Artifact 名に使う package_name は package.py が GITHUB_OUTPUT へ書き出す
      - name: 配布用 zip をパッケージ
        id: package
        env:
          MACOS_SIGN_IDENTITY: ${{ secrets.MACOS_SIGN_IDENTITY }}
          MACOS_TEAM_ID: ${{ secrets.MACOS_TEAM_ID }}
          MACOS_NOTARY_APPLE_ID: ${{ secrets.MACOS_NOTARY_APPLE_ID }}
          MACOS_NOTARY_PASSWORD: ${{ secrets.MACOS_NOTARY_PASSWORD }}
        run: python scripts/package.py --clean

      - uses: actions/upload-artifact@v4
        with:
          name: ${{ steps.package.outputs.package_name }}
          path: dist/*.zip
          if-no-files-found: error

  # 手動実行でタグを選んだ場合も Release を作らないよう、イベント種別まで確認する
  release:
    needs: [meta, build]
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/download-artifact@v4
        with:
          path: dist
          merge-multiple: true

      # サードパーティ Action は書き換え可能なタグではなく commit SHA で固定する
      - uses: softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65 # v2.6.2
        with:
          name: ${{ needs.meta.outputs.app_name }} v${{ needs.meta.outputs.app_version }}
          files: dist/*.zip
          generate_release_notes: true
```

- [ ] **Step 2: ワークフローの構文を検証**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml', encoding='utf-8')); print('OK')"`
Expected: `OK`

（`pyyaml` が無ければ `python -m pip install pyyaml` を先に実行する）

- [ ] **Step 3: `meta` ジョブのスクリプトをローカルで確認**

Run: `GITHUB_REF=refs/tags/v$(python -c "import sys; sys.path.insert(0,'scripts'); import appinfo; print(appinfo.read_version())") python -c "
import os, sys
sys.path.insert(0, 'scripts')
import appinfo
name, version = appinfo.read_app_name(), appinfo.read_version()
ref = os.environ.get('GITHUB_REF', '')
if ref.startswith('refs/tags/'):
    tag = ref.removeprefix('refs/tags/')
    if tag != f'v{version}':
        raise SystemExit('不一致')
print(f'app_name={name}')
print(f'app_version={version}')
"`
Expected: `app_name=MosaicTool` と `app_version=<現在のバージョン>` が出る

- [ ] **Step 4: コミット**

```bash
git add .github/workflows/release.yml
git commit -m "ci: Windows と macOS をビルドし Release 作成を独立ジョブへ分ける"
```

- [ ] **Step 5: 手動実行で両ジョブが通ることを確認**

PR を作成して main へマージしたあと、Actions から `release` ワークフローを `workflow_dispatch` で実行する。Windows / macOS 両方の Artifact が生成され、`release` ジョブがスキップされることを確認する。

（この確認は push 後にしかできないため、Task 12 まで終えてから実施してよい）

---

### Task 12: ドキュメントを更新する

macOS の導入手順、開発手順の変更（PowerShell → Python）、署名 Secrets の取得手順を書く。

**Files:**
- Modify: `README.md`, `docs/development.md`
- Create: `docs/macos-signing.md`

**Interfaces:**
- Consumes: これまでの全タスクの成果
- Produces: なし

- [ ] **Step 1: `README.md` の「導入」を書き換える**

既存の「## 導入」節を以下で置き換える。

```markdown
## 導入

[Releases](https://github.com/kidonaru/MosaicTool/releases) から使用中の OS 向けのファイルをダウンロードし、展開します。インストールは不要です。

| OS | ファイル | 起動方法 |
|---|---|---|
| Windows | `MosaicTool-v<バージョン>-win-x64.zip` | 展開して `MosaicTool.exe` を実行 |
| macOS (Apple Silicon) | `MosaicTool-v<バージョン>-mac-arm64.zip` | 展開して `MosaicTool.app` を実行(`/Applications` へ移動しても構いません) |

画像ファイルまたはフォルダは、アプリのアイコンへのドラッグ&ドロップ、コマンドライン引数、ウィンドウ(画像の上を含む)へのドラッグ&ドロップのいずれでも開けます。編集中に画像ファイルをドロップすると編集リストの末尾へ追加され、フォルダをドロップした場合は開き直します。

macOS で「開発元を確認できないため開けません」と表示される場合は、`MosaicTool.app` を右クリックして「開く」を選ぶか、以下を実行してください。

```
xattr -dr com.apple.quarantine /path/to/MosaicTool.app
```
```

- [ ] **Step 2: `README.md` のショートカット表を macOS 対応にする**

「## 操作」の表と「### ショートカットキー」の表で、`Ctrl` を使っている行を `Ctrl / Cmd` に書き換える（`Ctrl+Z` → `Ctrl+Z / Cmd+Z`、`Ctrl+S` → `Ctrl+S / Cmd+S`）。表の下の注記も「`Ctrl+S` 以外は」を「保存以外は」に書き換える。

- [ ] **Step 3: `README.md` の「保存先」と「自動検出」を更新する**

「## 保存先」のフォルダ例 `フォルダ名_mc\` を `フォルダ名_mc/` に直す。

「## 自動検出 (任意)」の手順 2 を以下へ置き換える。

```markdown
2. 初回は「セットアップ」を押します。推論用の実行環境と、標準の検出モデル 2 件
   (顔・目 / 合計 約 13MB) がダウンロードされます

   | OS | 実行環境の容量 | 置き場所 |
   |---|---|---|
   | Windows | CPU 版 約 250MB / GPU 版 約 2.5GB | `MosaicTool.exe` と同じ場所の `runtime` / `models` フォルダ |
   | macOS | 1 通りのみ(Apple Silicon の GPU を自動で使います) | `~/Library/Application Support/MosaicTool/` の `runtime` / `models` フォルダ |
```

- [ ] **Step 4: `docs/development.md` を更新する**

以下を書き換える。

- 「実行ファイルのビルド」: コマンドを `python scripts/build.py` にし、オプション表を `--python` / `--onedir` / `--clean` / `--uv-version` / `--sign-identity` に差し替える。macOS では常に `.app`（onedir）になること、`assets/icon.icns` が使われることを追記する
- 「配布用 zip のパッケージング」: コマンドを `just package --clean`（= `python scripts/package.py --clean`）にし、macOS の zip 構成を追記する

```
MosaicTool-v<バージョン>-mac-arm64.zip
└── MosaicTool-v<バージョン>-mac-arm64/
    ├── MosaicTool.app
    └── README.md
```

- 「リリース」: ワークフローが Windows と macOS の 2 ランナーでビルドし、`release` ジョブが両方の zip をまとめてアップロードすることを書く。スクリプト表を `python scripts/bump.py patch` / `python scripts/tag.py` に差し替える
- 「リポジトリ構成」: `scripts/` の説明を「ビルド・パッケージング・リリース用の Python スクリプト」に直し、`assets/` に `.icns` を追記する
- 末尾に macOS の署名について 1 行加え、`docs/macos-signing.md` へ誘導する

- [ ] **Step 5: `docs/macos-signing.md` を作成する**

```markdown
# macOS の署名と公証

`MosaicTool.app` は Developer ID で署名し、Apple の公証 (notarization) を通すことで、
ダウンロード後にダブルクリックで起動できるようになります。

GitHub Secrets が未設定の場合、ビルドは ad-hoc 署名のまま続行し公証をスキップします。
その場合はダウンロードしたユーザーが `xattr -dr com.apple.quarantine` を実行する必要があります。

## 必要なもの

- Apple Developer Program のメンバーシップ (年 $99)
- Developer ID Application 証明書
- 公証用の App-specific password

## 手順

### 1. 証明書を作る

1. Keychain Access で「証明書アシスタント」→「認証局に証明書を要求」を選び、
   CSR ファイル (`.certSigningRequest`) を保存する
2. [Apple Developer の Certificates](https://developer.apple.com/account/resources/certificates/list) で
   「Developer ID Application」を選び、CSR をアップロードして証明書をダウンロードする
3. ダウンロードした `.cer` をダブルクリックして Keychain へ登録する

### 2. 証明書を .p12 で書き出す

Keychain Access で証明書と秘密鍵をまとめて選び、右クリック →「2 項目を書き出す」で
`.p12` として保存する。書き出し時に設定したパスワードは次の手順で使う。

base64 に変換する。

```
base64 -i certificate.p12 | pbcopy
```

### 3. App-specific password を作る

[appleid.apple.com](https://appleid.apple.com/) にサインインし、
「サインインとセキュリティ」→「App 用パスワード」から発行する。

### 4. GitHub Secrets に登録する

リポジトリの Settings → Secrets and variables → Actions で以下を登録する。

| 名前 | 値 |
|---|---|
| `MACOS_CERTIFICATE` | 手順 2 で base64 化した `.p12` |
| `MACOS_CERTIFICATE_PWD` | `.p12` のパスワード |
| `MACOS_SIGN_IDENTITY` | `Developer ID Application: <名前> (<Team ID>)` |
| `MACOS_TEAM_ID` | Team ID (10 文字) |
| `MACOS_NOTARY_APPLE_ID` | Apple Developer アカウントの Apple ID |
| `MACOS_NOTARY_PASSWORD` | 手順 3 の App-specific password |

`MACOS_SIGN_IDENTITY` の正確な文字列は、証明書を登録した Mac で以下を実行すると確認できる。

```
security find-identity -v -p codesigning
```

## ローカルで署名する

上記の環境変数を設定してビルドすると、同じ経路で署名・公証される。

```
export MACOS_SIGN_IDENTITY="Developer ID Application: ... (TEAMID)"
export MACOS_TEAM_ID="TEAMID"
export MACOS_NOTARY_APPLE_ID="you@example.com"
export MACOS_NOTARY_PASSWORD="xxxx-xxxx-xxxx-xxxx"
python scripts/package.py --clean
```

## 確認

```
codesign --verify --deep --strict --verbose=2 dist/MosaicTool.app
spctl --assess --type execute --verbose dist/MosaicTool.app
xcrun stapler validate dist/MosaicTool.app
```

`spctl` が `accepted` かつ `source=Notarized Developer ID` を返せば成功。
```

- [ ] **Step 6: ドキュメントの記述がコードと一致することを確認**

Run: `just --list`
Expected: `build` / `package` / `bump` / `tag` / `release` / `run` / `version` が `docs/development.md` の記述どおりに並ぶ

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add README.md docs/development.md docs/macos-signing.md
git commit -m "docs: macOS 版の導入・ビルド・署名手順を追加する"
```

---

## 完了条件

- [ ] `python -m pytest -q` が macOS で全件パスする
- [ ] `python scripts/package.py --clean` が macOS で `dist/MosaicTool-v<ver>-mac-arm64.zip` を生成する
- [ ] 展開した `MosaicTool.app` が起動し、画像を開いて保存できる
- [ ] Finder から画像を `MosaicTool.app` にドロップして開ける
- [ ] `release` ワークフローの手動実行で Windows / macOS 両方の Artifact が生成される
- [ ] `scripts/*.ps1` が 1 本も残っていない

## 未検証のまま残る事項

| 項目 | 理由 | いつ確認できるか |
|---|---|---|
| Developer ID 署名・公証 | 証明書が未取得 | Secrets 登録後の初回リリース |
| Hardened Runtime 下での自動検出 | 公証済みビルドが必要 | 同上 |
| MPS での実推論 | torch とモデルのダウンロード (数百 MB) が必要 | Task 4 完了後に任意で実施 |
| Windows のリグレッション | 手元に Windows 環境がない | `release` ワークフローの手動実行 |
