"""macOS の署名・公証

Secrets が未設定でもビルドを通したいので、資格情報が無ければ黙って
スキップする(PyInstaller が付ける ad-hoc 署名のまま配る)。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import appinfo

# Mach-O のマジックナンバー(32/64bit の両エンディアンと fat バイナリ)
_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
}


def entitlements_path() -> Path:
    return Path(__file__).resolve().parent / "entitlements.plist"


def is_macho(path: Path) -> bool:
    """拡張子では判別できない実行ファイル(Qt framework 本体など)も拾う"""
    try:
        with open(path, "rb") as f:
            return f.read(4) in _MACHO_MAGICS
    except OSError:
        return False


def _framework_version_dir(path: Path) -> Path | None:
    """framework の中なら署名すべき Versions/X を返す"""
    for parent in path.parents:
        if parent.suffix == ".framework":
            try:
                relative = path.relative_to(parent)
            except ValueError:  # pragma: no cover - parents なので届かない
                return None
            if len(relative.parts) >= 2 and relative.parts[0] == "Versions":
                return parent / "Versions" / relative.parts[1]
            return parent
    return None


def macho_targets(app: Path) -> list[Path]:
    """署名対象を内側から順に並べる(外側を先に署名すると壊れる)

    framework の中身は個別に署名すると Gatekeeper に弾かれるため、
    バンドルとして Versions/X 単位でまとめて署名する。
    """
    targets: list[Path] = []
    seen: set[Path] = set()
    for p in app.rglob("*"):
        if p.is_symlink() or not p.is_file() or not is_macho(p):
            continue
        target = _framework_version_dir(p) or p
        if target not in seen:
            seen.add(target)
            targets.append(target)
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


def notarize_and_staple(app: Path, identity: str = "") -> bool:
    """公証して staple する。資格情報が無ければ何もせず False を返す

    公証は Developer ID 署名済みの .app しか受け付けない。署名 ID が無いまま
    投げると notarytool が失敗してパッケージ全体が落ちるため、先に弾く。
    """
    identity = identity or os.environ.get("MACOS_SIGN_IDENTITY", "")
    if not identity:
        print("-- 署名 ID が未設定のため公証をスキップします")
        return False
    credentials = notary_credentials()
    if credentials is None:
        print("-- 公証の資格情報が未設定のためスキップします")
        return False
    auth = [
        "--apple-id", credentials["apple_id"],
        "--team-id", credentials["team_id"],
        "--password", credentials["password"],
    ]
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "notarize.zip"
        appinfo.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
        # notarytool submit は結果が Invalid でも終了コード 0 を返すので、
        # status を自分で見て、駄目なら却下理由のログを出して止める。
        result = appinfo.run(
            ["xcrun", "notarytool", "submit", str(archive), *auth,
             "--wait", "--output-format", "json"],
            capture=True,
        )
        submission = json.loads(result.stdout)
        status = submission.get("status", "")
        submission_id = submission.get("id", "")
        print(f"-- 公証の結果: {status} (id: {submission_id})")
        if status != "Accepted":
            # id が取れないときにログを引くと、その失敗で本来の理由が埋もれる
            if submission_id:
                appinfo.run(["xcrun", "notarytool", "log", submission_id, *auth])
            appinfo.fail(f"公証が通りませんでした: {status or result.stdout}")
    # staple はチケットを .app に埋め込む(オフラインでも Gatekeeper を通る)
    appinfo.run(["xcrun", "stapler", "staple", str(app)])
    print(f"== 公証しました: {app} ==")
    return True
