import os
from src.core.roblox_manager import RobloxManager


def test_resolve_version_exe_found(tmp_path, monkeypatch):
    versions = tmp_path / "Versions"
    vdir = versions / "version-abc"
    vdir.mkdir(parents=True)
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: str(versions)))
    monkeypatch.setattr(RobloxManager, "get_stock_versions_root", staticmethod(lambda: None))
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
    monkeypatch.setattr(RobloxManager, "get_stock_versions_root", staticmethod(lambda: None))
    # bare guid (no "version-" prefix) resolves to the same exe
    assert RobloxManager.resolve_version_exe("abc") == str(vdir / "RobloxPlayerBeta.exe")


def test_resolve_version_exe_missing_returns_none(tmp_path, monkeypatch):
    versions = tmp_path / "Versions"
    versions.mkdir()
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: str(versions)))
    monkeypatch.setattr(RobloxManager, "get_stock_versions_root", staticmethod(lambda: None))
    assert RobloxManager.resolve_version_exe("version-nope") is None


def test_resolve_version_exe_no_versions_root(monkeypatch):
    monkeypatch.setattr(RobloxManager, "get_versions_root", staticmethod(lambda: None))
    monkeypatch.setattr(RobloxManager, "get_stock_versions_root", staticmethod(lambda: None))
    assert RobloxManager.resolve_version_exe("version-abc") is None


def test_resolve_version_exe_prefers_stock(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    stock_dir = local / "Roblox" / "Versions" / "version-abc"
    other_dir = tmp_path / "Bloxstrap" / "Versions" / "version-abc"
    stock_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    (stock_dir / "RobloxPlayerBeta.exe").write_bytes(b"stock")
    (other_dir / "RobloxPlayerBeta.exe").write_bytes(b"other")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_versions_root",
                        staticmethod(lambda: str(other_dir.parent)))
    exe = RobloxManager.resolve_version_exe("version-abc")
    assert exe == str(stock_dir / "RobloxPlayerBeta.exe")


def test_launch_specific_rewrites_join_channel_and_pins(tmp_path, monkeypatch):
    from src.core import roblox_manager as rm_mod
    from src.core.version_changer import channel

    versions = tmp_path / "Versions"
    vdir = versions / "version-abc"
    vdir.mkdir(parents=True)
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    monkeypatch.setattr(RobloxManager, "get_versions_root",
                        staticmethod(lambda: str(versions)))
    monkeypatch.setattr(RobloxManager, "get_stock_versions_root",
                        staticmethod(lambda: None))
    pinned = []
    monkeypatch.setattr(channel, "pin_production_channel",
                        lambda: pinned.append(True) or True)
    captured = {}

    def fake_create_process(app_name, cmdline, *args, **kwargs):
        captured["app_name"] = app_name
        captured["cmdline"] = cmdline
        return 1

    monkeypatch.setattr(rm_mod._k32, "CreateProcessW", fake_create_process)

    uri = (
        "roblox-player:1+launchmode:play+channel:zlive"
        "+placelauncherurl:https%3A%2F%2Fwww.roblox.com%2FGame%2FPlaceLauncher.ashx"
    )
    ok, _pid = RobloxManager().launch_specific_version("version-abc", args=uri)

    assert ok is True
    assert pinned == [True]
    assert captured["app_name"] == str(vdir / "RobloxPlayerBeta.exe")
    assert "channel:production" in captured["cmdline"]
    assert "channel:zlive" not in captured["cmdline"]
    assert "placelauncherurl:https%3A%2F%2Fwww.roblox.com" in captured["cmdline"]


def test_launch_specific_skips_pin_when_build_missing(tmp_path, monkeypatch):
    from src.core.version_changer import channel

    versions = tmp_path / "Versions"
    versions.mkdir()
    monkeypatch.setattr(RobloxManager, "get_versions_root",
                        staticmethod(lambda: str(versions)))
    monkeypatch.setattr(RobloxManager, "get_stock_versions_root",
                        staticmethod(lambda: None))
    pinned = []
    monkeypatch.setattr(channel, "pin_production_channel",
                        lambda: pinned.append(True) or True)

    ok, pid = RobloxManager().launch_specific_version("version-nope")

    assert ok is False
    assert pid == 0
    assert pinned == []
