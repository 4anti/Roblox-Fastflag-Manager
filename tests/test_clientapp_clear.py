import json
import os

from src.core.roblox_manager import RobloxManager


def _mk_version(tmp_path, name, flags):
    """Create a fake <version>/ClientSettings/ClientAppSettings.json with flags."""
    vdir = tmp_path / name
    cs = vdir / "ClientSettings"
    cs.mkdir(parents=True)
    (cs / "ClientAppSettings.json").write_text(json.dumps(flags), encoding="utf-8")
    return str(vdir)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_has_flags_true_when_version_file_populated(tmp_path, monkeypatch):
    vdir = _mk_version(tmp_path, "version-aaa", {"FFlagX": True})
    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs", staticmethod(lambda: [vdir]))
    monkeypatch.setattr(RobloxManager, "global_clientapp_path", staticmethod(lambda: None))
    assert RobloxManager.clientapp_json_has_flags() is True


def test_has_flags_false_when_all_empty(tmp_path, monkeypatch):
    vdir = _mk_version(tmp_path, "version-bbb", {})
    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs", staticmethod(lambda: [vdir]))
    monkeypatch.setattr(RobloxManager, "global_clientapp_path", staticmethod(lambda: None))
    assert RobloxManager.clientapp_json_has_flags() is False


def test_clear_empties_all_version_files_and_global(tmp_path, monkeypatch):
    # Arrange: two populated per-version files + a populated legacy global file.
    v1 = _mk_version(tmp_path, "version-1", {"FFlagA": True, "DFIntB": 5})
    v2 = _mk_version(tmp_path, "version-2", {"FFlagA": True})
    global_file = tmp_path / "global" / "ClientSettings" / "ClientAppSettings.json"
    global_file.parent.mkdir(parents=True)
    global_file.write_text(json.dumps({"FFlagLeftover": True}), encoding="utf-8")

    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs", staticmethod(lambda: [v1, v2]))
    monkeypatch.setattr(RobloxManager, "global_clientapp_path", staticmethod(lambda: str(global_file)))

    assert RobloxManager.clientapp_json_has_flags() is True

    # Act
    ok, msg = RobloxManager.clear_fflags_json()

    # Assert: every managed file is now empty, including the global one.
    assert ok is True
    assert _read(os.path.join(v1, "ClientSettings", "ClientAppSettings.json")) == {}
    assert _read(os.path.join(v2, "ClientSettings", "ClientAppSettings.json")) == {}
    assert _read(str(global_file)) == {}
    assert RobloxManager.clientapp_json_has_flags() is False


def test_clear_does_not_create_missing_global(tmp_path, monkeypatch):
    v1 = _mk_version(tmp_path, "version-1", {"FFlagA": True})
    missing_global = tmp_path / "noexist" / "ClientSettings" / "ClientAppSettings.json"
    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs", staticmethod(lambda: [v1]))
    monkeypatch.setattr(RobloxManager, "global_clientapp_path", staticmethod(lambda: str(missing_global)))

    ok, _ = RobloxManager.clear_fflags_json()

    assert ok is True
    assert _read(os.path.join(v1, "ClientSettings", "ClientAppSettings.json")) == {}
    # The global file must NOT be created where it never existed.
    assert not missing_global.exists()


# --- write-scoping: never touch a third-party bootstrapper's install ---

def test_writable_dirs_are_stock_only(tmp_path, monkeypatch):
    """get_writable_version_dirs must include STOCK Roblox builds and EXCLUDE any
    third-party bootstrapper install (Froststrap/Bloxstrap/etc), so FFM never
    overwrites their ClientAppSettings.json / mods."""
    local = tmp_path / "Local"
    # stock build
    stock = local / "Roblox" / "Versions" / "version-stock"
    stock.mkdir(parents=True)
    (stock / "RobloxPlayerBeta.exe").write_text("x", encoding="utf-8")
    # third-party (Froststrap) build
    frost = local / "Froststrap" / "Versions" / "version-frost"
    frost.mkdir(parents=True)
    (frost / "RobloxPlayerBeta.exe").write_text("x", encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(local))

    writable = RobloxManager.get_writable_version_dirs()
    assert str(stock) in writable
    assert str(frost) not in writable
    assert all("Froststrap" not in p for p in writable)


def test_apply_and_clear_skip_third_party(tmp_path, monkeypatch):
    """A full apply→clear cycle must leave a third-party build's flags untouched."""
    local = tmp_path / "Local"
    stock = local / "Roblox" / "Versions" / "version-stock"
    stock.mkdir(parents=True)
    (stock / "RobloxPlayerBeta.exe").write_text("x", encoding="utf-8")
    frost = local / "Froststrap" / "Versions" / "version-frost"
    (frost / "ClientSettings").mkdir(parents=True)
    (frost / "RobloxPlayerBeta.exe").write_text("x", encoding="utf-8")
    frost_settings = frost / "ClientSettings" / "ClientAppSettings.json"
    frost_settings.write_text(json.dumps({"FFlagFroststrapOwned": "true"}), encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "global_clientapp_path", staticmethod(lambda: None))

    RobloxManager.apply_fflags_json({"FFlagFFM": "true"})
    RobloxManager.clear_fflags_json()

    # Stock got written+cleared; Froststrap's file is byte-for-byte untouched.
    assert _read(str(frost_settings)) == {"FFlagFroststrapOwned": "true"}
    assert _read(os.path.join(str(stock), "ClientSettings", "ClientAppSettings.json")) == {}
