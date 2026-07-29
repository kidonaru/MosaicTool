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
