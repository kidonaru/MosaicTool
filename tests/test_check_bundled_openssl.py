"""同梱 OpenSSL の組み合わせ検証の確認"""
from pathlib import Path

import check_bundled_openssl as checker


def _toc(entries) -> str:
    # PyInstaller の TOC は入れ子のタプルを含む Python リテラル
    return repr((r"C:\dist\App.exe", False, [], tuple(entries), "python310.dll"))


def test_pairs_from_the_same_directory_are_accepted():
    toc = _toc([
        (r"libssl-3-x64.dll", r"C:\ssl\bin\libssl-3-x64.dll", "BINARY"),
        (r"libcrypto-3-x64.dll", r"C:\ssl\bin\libcrypto-3-x64.dll", "BINARY"),
    ])
    assert checker.find_mismatches(toc) == []


def test_pair_from_different_directories_is_reported():
    toc = _toc([
        (r"libssl-3-x64.dll", r"C:\Windows\System32\libssl-3-x64.dll", "BINARY"),
        (r"libcrypto-3-x64.dll", r"C:\Git\mingw64\bin\libcrypto-3-x64.dll", "BINARY"),
    ])
    mismatches = checker.find_mismatches(toc)
    assert len(mismatches) == 1
    assert "-3-x64.dll" in mismatches[0]


def test_directory_comparison_ignores_case():
    toc = _toc([
        (r"libssl-3-x64.dll", r"C:\SSL\BIN\libssl-3-x64.dll", "BINARY"),
        (r"libcrypto-3-x64.dll", r"c:\ssl\bin\libcrypto-3-x64.dll", "BINARY"),
    ])
    assert checker.find_mismatches(toc) == []


def test_families_are_checked_independently():
    # 1.1 系と 3 系が別の場所から来るのは正常(それぞれ別モジュール)
    toc = _toc([
        (r"libssl-1_1.dll", r"C:\py\DLLs\libssl-1_1.dll", "BINARY"),
        (r"libcrypto-1_1.dll", r"C:\py\DLLs\libcrypto-1_1.dll", "BINARY"),
        (r"libssl-3-x64.dll", r"C:\ssl\bin\libssl-3-x64.dll", "BINARY"),
        (r"libcrypto-3-x64.dll", r"C:\ssl\bin\libcrypto-3-x64.dll", "BINARY"),
    ])
    assert checker.find_mismatches(toc) == []


def test_lone_library_is_not_a_mismatch():
    toc = _toc([(r"libcrypto-3-x64.dll", r"C:\ssl\bin\libcrypto-3-x64.dll", "BINARY")])
    assert checker.find_mismatches(toc) == []


def test_non_binary_entries_are_ignored():
    toc = _toc([
        (r"libssl-3-x64.dll", r"C:\a\libssl-3-x64.dll", "DATA"),
        (r"libcrypto-3-x64.dll", r"C:\b\libcrypto-3-x64.dll", "DATA"),
    ])
    assert checker.openssl_sources(toc) == {}


def test_entries_nested_deeper_are_found():
    toc = repr((("meta",), [[(r"libssl-3.dll", r"C:\x\libssl-3.dll", "BINARY")]]))
    assert checker.openssl_sources(toc) == {"-3.dll": {"libssl": Path(r"C:\x\libssl-3.dll")}}


def test_main_fails_on_mismatch(tmp_path, capsys):
    toc_file = tmp_path / "EXE-00.toc"
    toc_file.write_text(_toc([
        (r"libssl-3-x64.dll", r"C:\a\libssl-3-x64.dll", "BINARY"),
        (r"libcrypto-3-x64.dll", r"C:\b\libcrypto-3-x64.dll", "BINARY"),
    ]), encoding="utf-8")
    assert checker.main(["check", str(toc_file)]) == 1
    assert "OpenSSL" in capsys.readouterr().err


def test_main_succeeds_on_consistent_bundle(tmp_path):
    toc_file = tmp_path / "EXE-00.toc"
    toc_file.write_text(_toc([
        (r"libssl-3-x64.dll", r"C:\a\libssl-3-x64.dll", "BINARY"),
        (r"libcrypto-3-x64.dll", r"C:\a\libcrypto-3-x64.dll", "BINARY"),
    ]), encoding="utf-8")
    assert checker.main(["check", str(toc_file)]) == 0
