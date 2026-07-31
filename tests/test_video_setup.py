"""ffmpeg セットアップ(zip からの配置と取得計画)の検証"""
import zipfile

import pytest

from mosaic_tool.video import ffmpeg
from mosaic_tool.video.setup_dialog import install_from_zip, planned_downloads


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """runtime/ffmpeg の配置先をテスト用ディレクトリへ向ける"""
    monkeypatch.setattr(
        "mosaic_tool.video.ffmpeg.runtime_dir", lambda: tmp_path / "runtime"
    )
    return tmp_path


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


class TestInstallFromZip:
    def test_extracts_nested_binaries(self, runtime):
        # gyan.dev のビルドは bin/ 配下に実行ファイルを置く
        src = make_zip(
            runtime / "dl.zip",
            {
                "ffmpeg-7.0-essentials_build/bin/ffmpeg.exe": b"bin1",
                "ffmpeg-7.0-essentials_build/bin/ffprobe.exe": b"bin2",
                "ffmpeg-7.0-essentials_build/README.txt": b"doc",
            },
        )
        install_from_zip(src, ("ffmpeg.exe", "ffprobe.exe"))
        assert (ffmpeg.ffmpeg_dir() / "ffmpeg.exe").read_bytes() == b"bin1"
        assert (ffmpeg.ffmpeg_dir() / "ffprobe.exe").read_bytes() == b"bin2"

    def test_extracts_flat_binary(self, runtime):
        # evermeet.cx のビルドは実行ファイルが zip 直下にある
        src = make_zip(runtime / "dl.zip", {"ffmpeg": b"bin"})
        install_from_zip(src, ("ffmpeg",))
        assert (ffmpeg.ffmpeg_dir() / "ffmpeg").read_bytes() == b"bin"

    def test_missing_binary_raises(self, runtime):
        src = make_zip(runtime / "dl.zip", {"README.txt": b"doc"})
        with pytest.raises(ffmpeg.VideoError):
            install_from_zip(src, ("ffmpeg.exe",))


def test_planned_downloads_cover_both_binaries():
    plans = planned_downloads()
    binaries = {name for plan in plans for name in plan.binaries}
    assert len(binaries) == 2
    assert all(plan.url.startswith("https://") for plan in plans)


def test_planned_downloads_are_pinned_and_verified():
    """実行ファイルの配布経路は、バージョン固定 URL + SHA-256 で検証する"""
    for plan in planned_downloads():
        assert len(plan.sha256) == 64
        assert all(c in "0123456789abcdef" for c in plan.sha256)
        # "latest" 系のローリング URL では内容検証が成立しない
        assert "latest" not in plan.url and "getrelease" not in plan.url
