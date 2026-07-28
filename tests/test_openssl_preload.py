"""同梱 OpenSSL の先行ロードの検証"""
import sys

import pytest

from mosaic_tool import openssl_preload


@pytest.fixture
def fake_windll(monkeypatch):
    """ctypes.WinDLL を差し替え、ロードされたパスを記録する"""
    loaded: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")
    # ctypes.WinDLL は非 Windows には存在しないため raising=False で属性未定義を許す
    monkeypatch.setattr(
        openssl_preload.ctypes, "WinDLL", loaded.append, raising=False
    )
    return loaded


def _touch(directory, names):
    for name in names:
        (directory / name).write_bytes(b"")


def test_does_nothing_when_not_frozen(monkeypatch, fake_windll):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert openssl_preload.preload_bundled_openssl() == []
    assert fake_windll == []


def test_does_nothing_when_bundle_has_no_openssl(monkeypatch, tmp_path, fake_windll):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert openssl_preload.preload_bundled_openssl() == []
    assert fake_windll == []


def test_loads_bundled_dlls_by_absolute_path(monkeypatch, tmp_path, fake_windll):
    _touch(tmp_path, ["libcrypto-3-x64.dll", "libssl-3-x64.dll"])
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    loaded = openssl_preload.preload_bundled_openssl()

    # libssl は libcrypto に依存するため crypto を先にロードする
    assert [p.name for p in loaded] == ["libcrypto-3-x64.dll", "libssl-3-x64.dll"]
    # 名前ではなくフルパスで固定できていないと System32 側に解決されうる
    assert fake_windll == [str(tmp_path / n) for n in
                           ("libcrypto-3-x64.dll", "libssl-3-x64.dll")]


def test_loads_only_the_dlls_that_exist(monkeypatch, tmp_path, fake_windll):
    _touch(tmp_path, ["libcrypto-3.dll", "libssl-3.dll"])
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    loaded = openssl_preload.preload_bundled_openssl()

    assert [p.name for p in loaded] == ["libcrypto-3.dll", "libssl-3.dll"]


def test_load_failure_does_not_raise(monkeypatch, tmp_path):
    _touch(tmp_path, ["libcrypto-3-x64.dll", "libssl-3-x64.dll"])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    def fail(_path):
        raise OSError("ロードできません")

    # raising=False の理由は fake_windll フィクスチャと同じ
    monkeypatch.setattr(openssl_preload.ctypes, "WinDLL", fail, raising=False)
    # 起動そのものを止めないこと(Qt の既定探索に委ねる)
    assert openssl_preload.preload_bundled_openssl() == []


def test_does_nothing_on_non_windows(monkeypatch, tmp_path, fake_windll):
    _touch(tmp_path, ["libcrypto-3-x64.dll", "libssl-3-x64.dll"])
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert openssl_preload.preload_bundled_openssl() == []
    assert fake_windll == []
