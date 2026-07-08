import os
from src.core.roblox_manager import RobloxManager


def test_resolve_version_exe_found(tmp_path, monkeypatch):
    versions = tmp_path / "Versions"
    vdir = versions / "version-abc"
    vdir.mkdir(parents=True)
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: str(versions)))
    # Act
    exe = RobloxManager.resolve_version_exe("version-abc")
    # Assert
    assert exe == str(vdir / "RobloxPlayerBeta.exe")


def test_resolve_version_exe_accepts_bare_guid(tmp_path, monkeypatch):
    versions = tmp_path / "Versions"
    vdir = versions / "version-abc"
    vdir.mkdir(parents=True)
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: str(versions)))
    # bare guid (no "version-" prefix) resolves to the same exe
    assert RobloxManager.resolve_version_exe("abc") == str(vdir / "RobloxPlayerBeta.exe")


def test_resolve_version_exe_missing_returns_none(tmp_path, monkeypatch):
    versions = tmp_path / "Versions"
    versions.mkdir()
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: str(versions)))
    assert RobloxManager.resolve_version_exe("version-nope") is None


def test_resolve_version_exe_no_versions_root(monkeypatch):
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: None))
    assert RobloxManager.resolve_version_exe("version-abc") is None
