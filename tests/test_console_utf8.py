"""ビルド用スクリプトが cp1252 コンソールでも落ちないことの検証"""
import subprocess
import sys
from pathlib import Path

import pytest

import console_utf8

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
# 日本語を出力する経路を通す最小の入力。
# 標準出力は既定で UnicodeEncodeError になり、標準エラーは既定の backslashreplace
# でエスケープされて読めなくなる。どちらも CI では困るので両方を検証する
SPEC_INPUT = "a = Analysis(['x.py'])\npyz = PYZ(a.pure)\n"
TOC_INPUT = repr(
    (r"C:\dist\App.exe", ((r"PySide6\plugins\tls\qopensslbackend.dll", r"C:\a", "BINARY"),))
)
SCRIPTS = [
    ("exclude_openssl_backend.py", "App.spec", SPEC_INPUT, 0),
    ("check_bundled_openssl.py", "EXE-00.toc", TOC_INPUT, 1),
]


class _StreamWithoutReconfigure:
    """pytest 等が差し替えたストリームの代役"""


def test_use_utf8_output_skips_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _StreamWithoutReconfigure())
    monkeypatch.setattr(sys, "stderr", _StreamWithoutReconfigure())
    use_utf8_output = console_utf8.use_utf8_output
    use_utf8_output()  # 例外を出さずに素通りすること


def test_use_utf8_output_switches_streams(monkeypatch):
    calls = []

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(sys, "stdout", FakeStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())
    console_utf8.use_utf8_output()
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2


@pytest.mark.parametrize("script,input_name,input_text,expected_code", SCRIPTS)
def test_japanese_output_survives_cp1252_console(
    tmp_path, script, input_name, input_text, expected_code
):
    """CI(GitHub Actions の Windows ランナー)の cp1252 出力を模した実行"""
    input_file = tmp_path / input_name
    input_file.write_text(input_text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), str(input_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PYTHONIOENCODING": "cp1252", "SYSTEMROOT": "C:\\Windows", "PATH": ""},
    )
    output = result.stdout + result.stderr
    assert "UnicodeEncodeError" not in output
    # backslashreplace でエスケープされていない = そのまま読める
    assert "\\u" not in output
    assert result.returncode == expected_code, output
