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
    args = build.makespec_args("MosaicTool", one_dir=False)
    assert "--onedir" in args
    assert "--onefile" not in args


def test_windows_build_defaults_to_onefile(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    args = build.makespec_args("MosaicTool", one_dir=False)
    assert "--onefile" in args


def test_makespec_does_not_take_build_only_options(monkeypatch):
    """--noconfirm / --clean は makespec が受け付けないため build 側で渡す"""
    monkeypatch.setattr(sys, "platform", "win32")
    args = build.makespec_args("MosaicTool", one_dir=False)
    assert "--noconfirm" not in args
    assert "--clean" not in args
    assert build.build_args(build.spec_path("MosaicTool"))[:4] == [
        "-m", "PyInstaller", "--noconfirm", "--clean",
    ]


def test_build_runs_from_the_generated_spec():
    """spec を編集してから使うため、ソースではなく spec を渡すこと"""
    spec = build.spec_path("MosaicTool")
    assert spec.name == "MosaicTool.spec"
    assert build.build_args(spec)[-1] == str(spec)


def test_pyinstaller_bundles_uv_icon_and_worker(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    joined = " ".join(build.makespec_args("MosaicTool", one_dir=True))
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
