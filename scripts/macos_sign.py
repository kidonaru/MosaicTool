"""macOS の署名・公証

Secrets が未設定でもビルドを通したいので、資格情報が無ければ黙って
スキップする(PyInstaller が付ける ad-hoc 署名のまま配る)。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import appinfo

# 署名対象とみなす拡張子(拡張子なしの実行ファイルは Contents/MacOS で拾う)
_BINARY_SUFFIXES = {".dylib", ".so"}


def entitlements_path() -> Path:
    return Path(__file__).resolve().parent / "entitlements.plist"


def macho_targets(app: Path) -> list[Path]:
    """署名対象を内側から順に並べる(外側を先に署名すると壊れる)"""
    targets = [
        p for p in app.rglob("*")
        if p.is_file() and not p.is_symlink()
        and (p.suffix in _BINARY_SUFFIXES or p.parent.name == "MacOS")
    ]
    # パスの深い順 → 同じ深さは名前順で安定させる
    targets.sort(key=lambda p: (-len(p.parts), str(p)))
    return [*targets, app]


def codesign_command(identity: str, target: Path) -> list[str]:
    return [
        "codesign",
        "--force",
        "--timestamp",
        "--options", "runtime",
        "--entitlements", str(entitlements_path()),
        "--sign", identity,
        str(target),
    ]


def sign_app(app: Path, identity: str = "") -> bool:
    """Developer ID で署名する。ID が無ければ何もせず False を返す"""
    identity = identity or os.environ.get("MACOS_SIGN_IDENTITY", "")
    if not identity:
        print("-- 署名 ID が未設定のため ad-hoc 署名のままにします")
        return False
    for target in macho_targets(app):
        appinfo.run(codesign_command(identity, target))
    appinfo.run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    print(f"== 署名しました: {app} ==")
    return True


def notary_credentials() -> dict[str, str] | None:
    """公証に必要な 3 つが揃っているときだけ返す"""
    apple_id = os.environ.get("MACOS_NOTARY_APPLE_ID", "")
    password = os.environ.get("MACOS_NOTARY_PASSWORD", "")
    team_id = os.environ.get("MACOS_TEAM_ID", "")
    if not (apple_id and password and team_id):
        return None
    return {"apple_id": apple_id, "password": password, "team_id": team_id}


def notarize_and_staple(app: Path) -> bool:
    """公証して staple する。資格情報が無ければ何もせず False を返す"""
    credentials = notary_credentials()
    if credentials is None:
        print("-- 公証の資格情報が未設定のためスキップします")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "notarize.zip"
        appinfo.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
        appinfo.run([
            "xcrun", "notarytool", "submit", str(archive),
            "--apple-id", credentials["apple_id"],
            "--team-id", credentials["team_id"],
            "--password", credentials["password"],
            "--wait",
        ])
    # staple はチケットを .app に埋め込む(オフラインでも Gatekeeper を通る)
    appinfo.run(["xcrun", "stapler", "staple", str(app)])
    print(f"== 公証しました: {app} ==")
    return True
