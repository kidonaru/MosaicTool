"""署名・公証のコマンド組み立てと資格情報の判定の検証(codesign は実行しない)"""
from pathlib import Path

import macos_sign


def test_entitlements_file_exists():
    path = macos_sign.entitlements_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    # venv の Python を子プロセスとして起動するために必要
    assert "com.apple.security.cs.disable-library-validation" in text
    assert "com.apple.security.cs.allow-jit" in text
    assert "com.apple.security.cs.allow-unsigned-executable-memory" in text


def test_codesign_command_uses_hardened_runtime():
    cmd = macos_sign.codesign_command("Developer ID Application: X (TEAM)", Path("/a/b"))
    assert cmd[0] == "codesign"
    assert "--force" in cmd
    assert "--timestamp" in cmd
    assert cmd[cmd.index("--options") + 1] == "runtime"
    assert cmd[cmd.index("--sign") + 1] == "Developer ID Application: X (TEAM)"
    assert cmd[-1] == "/a/b"


_MACHO = b"\xcf\xfa\xed\xfe" + b"\x00" * 16


def _write_macho(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_MACHO)
    return path


def test_macho_targets_are_deepest_first(tmp_path):
    """入れ子のバイナリを内側から署名する(外側を先に署名すると壊れる)"""
    app = tmp_path / "MosaicTool.app"
    inner = _write_macho(app / "Contents" / "Frameworks" / "sub" / "deep.dylib")
    shallow = _write_macho(app / "Contents" / "Frameworks" / "lib.so")
    exe = _write_macho(app / "Contents" / "MacOS" / "MosaicTool")

    targets = macos_sign.macho_targets(app)

    assert targets[-1] == app          # バンドル本体は最後
    assert targets.index(inner) < targets.index(shallow)
    assert exe in targets


def test_macho_targets_include_extensionless_binaries(tmp_path):
    """拡張子の無い実行ファイル(uv など)も署名対象にする

    署名から漏れると ad-hoc 署名のまま公証に出て Invalid になる。
    """
    app = tmp_path / "MosaicTool.app"
    uv = _write_macho(app / "Contents" / "Frameworks" / "uv")
    text = app / "Contents" / "Resources" / "readme.txt"
    text.parent.mkdir(parents=True)
    text.write_text("not a binary", encoding="utf-8")

    targets = macos_sign.macho_targets(app)

    assert uv in targets
    assert text not in targets


def test_macho_targets_sign_frameworks_as_bundles(tmp_path):
    """framework は中の実行ファイルではなく Versions/A をまとめて署名する"""
    app = tmp_path / "MosaicTool.app"
    framework = app / "Contents" / "Frameworks" / "QtCore.framework"
    binary = _write_macho(framework / "Versions" / "A" / "QtCore")
    _write_macho(framework / "Versions" / "A" / "helper.dylib")

    targets = macos_sign.macho_targets(app)

    assert framework / "Versions" / "A" in targets
    assert binary not in targets
    # 同じ framework を二重に署名しない
    assert len([p for p in targets if ".framework" in str(p)]) == 1


def test_notary_credentials_requires_all_values(monkeypatch):
    for key in ("MACOS_NOTARY_APPLE_ID", "MACOS_NOTARY_PASSWORD", "MACOS_TEAM_ID"):
        monkeypatch.delenv(key, raising=False)
    assert macos_sign.notary_credentials() is None

    monkeypatch.setenv("MACOS_NOTARY_APPLE_ID", "a@example.com")
    monkeypatch.setenv("MACOS_NOTARY_PASSWORD", "pw")
    assert macos_sign.notary_credentials() is None  # TEAM_ID が欠けている

    monkeypatch.setenv("MACOS_TEAM_ID", "TEAM")
    assert macos_sign.notary_credentials() == {
        "apple_id": "a@example.com", "password": "pw", "team_id": "TEAM",
    }


def test_sign_app_is_skipped_without_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("MACOS_SIGN_IDENTITY", raising=False)
    assert macos_sign.sign_app(tmp_path / "MosaicTool.app", "") is False


def test_notarize_is_skipped_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("MACOS_SIGN_IDENTITY", "Developer ID Application: X (TEAM)")
    for key in ("MACOS_NOTARY_APPLE_ID", "MACOS_NOTARY_PASSWORD", "MACOS_TEAM_ID"):
        monkeypatch.delenv(key, raising=False)
    assert macos_sign.notarize_and_staple(tmp_path / "MosaicTool.app") is False


def test_notarize_is_skipped_without_sign_identity(monkeypatch, tmp_path):
    """署名 ID が無ければ、公証の資格情報が揃っていても投げない

    ad-hoc 署名の .app を notarytool へ送っても必ず失敗するため。
    """
    monkeypatch.delenv("MACOS_SIGN_IDENTITY", raising=False)
    monkeypatch.setenv("MACOS_NOTARY_APPLE_ID", "a@example.com")
    monkeypatch.setenv("MACOS_NOTARY_PASSWORD", "pw")
    monkeypatch.setenv("MACOS_TEAM_ID", "TEAM")
    assert macos_sign.notarize_and_staple(tmp_path / "MosaicTool.app") is False
