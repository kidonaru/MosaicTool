"""spec からの Qt OpenSSL バックエンド除外の検証"""
import pytest

import exclude_openssl_backend as excluder

SPEC_TEMPLATE = """a = Analysis(['mosaic_tool/__main__.py'])
{extra}pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas)
"""


def test_patch_inserts_filter_before_pyz():
    patched = excluder.patch(SPEC_TEMPLATE.format(extra=""))
    body = patched.split("pyz = PYZ(")[0]
    # a.binaries が確定した後、EXE へ渡される前に絞り込む必要がある
    assert "a.binaries = [" in body
    assert excluder.EXCLUDED_PLUGIN in body


def test_patched_spec_filters_the_plugin_entry():
    # 差し込んだ行を実際に実行し、生成コードが構文的にも意図通り動くことを確認する
    patched = excluder.patch(SPEC_TEMPLATE.format(extra=""))
    filter_line = next(l for l in patched.splitlines() if l.startswith("a.binaries ="))

    class FakeAnalysis:
        binaries = [
            ("PySide6\\plugins\\tls\\qopensslbackend.dll", "C:\\x", "BINARY"),
            ("PySide6\\plugins\\tls\\qschannelbackend.dll", "C:\\x", "BINARY"),
        ]

    namespace = {"a": FakeAnalysis}
    exec(filter_line, namespace)
    assert [b[0] for b in namespace["a"].binaries] == [
        "PySide6\\plugins\\tls\\qschannelbackend.dll"
    ]


def test_patch_is_idempotent():
    once = excluder.patch(SPEC_TEMPLATE.format(extra=""))
    assert excluder.patch(once) == once


def test_plugin_name_elsewhere_is_not_mistaken_for_applied():
    # spec の別の箇所に名前が現れただけで適用済みと誤判定してはいけない
    spec = SPEC_TEMPLATE.format(extra="# note: qopensslbackend は同梱しない方針\n")
    assert excluder.EXCLUDED_PLUGIN in spec
    assert "a.binaries = [" in excluder.patch(spec)


def test_patch_raises_when_anchor_is_missing():
    with pytest.raises(ValueError, match="pyz = PYZ"):
        excluder.patch("a = Analysis([])\nexe = EXE(a.binaries)\n")


def test_main_writes_the_patched_spec(tmp_path):
    spec = tmp_path / "App.spec"
    spec.write_text(SPEC_TEMPLATE.format(extra=""), encoding="utf-8")
    assert excluder.main(["exclude", str(spec)]) == 0
    assert excluder.EXCLUDED_PLUGIN in spec.read_text(encoding="utf-8")


def test_main_is_safe_to_rerun(tmp_path, capsys):
    spec = tmp_path / "App.spec"
    spec.write_text(SPEC_TEMPLATE.format(extra=""), encoding="utf-8")
    excluder.main(["exclude", str(spec)])
    before = spec.read_text(encoding="utf-8")
    assert excluder.main(["exclude", str(spec)]) == 0
    assert spec.read_text(encoding="utf-8") == before
    assert "適用済み" in capsys.readouterr().out
