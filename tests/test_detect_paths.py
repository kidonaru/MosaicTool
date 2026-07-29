"""exe 隣を基準にしたパス解決の検証"""
import sys

from mosaic_tool.detect import paths


def test_base_dir_is_repo_root_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    # ソース実行時は mosaic_tool パッケージの 1 つ上(リポジトリ直下)
    assert (paths.base_dir() / "mosaic_tool").is_dir()


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


def test_uv_exe_name_differs_by_os(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.uv_exe_name() == "uv.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.uv_exe_name() == "uv"


def test_models_and_runtime_are_next_to_base(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert paths.models_dir() == tmp_path / "models"
    assert paths.runtime_dir() == tmp_path / "runtime"


def test_model_files_lists_pt_files_sorted(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    (models / "b.pt").write_bytes(b"")
    (models / "a.pt").write_bytes(b"")
    (models / "readme.txt").write_text("メモ", encoding="utf-8")
    assert [p.name for p in paths.model_files()] == ["a.pt", "b.pt"]


def test_model_files_is_empty_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert paths.model_files() == []


def test_runtime_is_not_ready_without_venv_python(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert not paths.is_runtime_ready()


def test_runtime_is_ready_when_venv_python_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    python = paths.venv_python()
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert paths.is_runtime_ready()


def test_worker_script_source_exists_in_package():
    # ワーカー本体はパッケージに同梱されている
    assert paths.worker_script_source().name == "worker_main.py"
    assert paths.worker_script_source().is_file()


def test_worker_script_is_installed_into_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "base_dir", lambda: tmp_path)
    assert paths.worker_script_installed().parent == tmp_path / "runtime"


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
