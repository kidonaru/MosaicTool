"""bump の対象バージョン決定の検証(git 操作は行わない)"""
import pytest

import appinfo
import bump


def test_resolve_target_bumps_patch(monkeypatch):
    monkeypatch.setattr(appinfo, "read_version", lambda: "1.2.3")
    assert bump.resolve_target("patch") == ("1.2.3", "1.2.4")


def test_resolve_target_rejects_same_version(monkeypatch):
    monkeypatch.setattr(appinfo, "read_version", lambda: "1.2.3")
    with pytest.raises(SystemExit):
        bump.resolve_target("1.2.3")


def test_resolve_target_rejects_malformed_current(monkeypatch):
    monkeypatch.setattr(appinfo, "read_version", lambda: "1.2")
    with pytest.raises(SystemExit):
        bump.resolve_target("patch")
