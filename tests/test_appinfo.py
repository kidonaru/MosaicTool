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
