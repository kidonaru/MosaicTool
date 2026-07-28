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
