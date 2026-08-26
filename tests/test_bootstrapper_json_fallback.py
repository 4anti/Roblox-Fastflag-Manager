"""Regression guard for the bootstrapper JSON merge fallback.

When a user has ONLY a bootstrapper install (Fishstrap / Bloxstrap /
Froststrap / Voidstrap / Plexity — no stock Roblox), `apply_fflags_json`
used to short-circuit with `[-] JSON: No Roblox version directories found`
because `get_writable_version_dirs` deliberately excludes bootstrapper
dirs. The apply then fell straight to Step 2 (live memory), which crashed
whenever offsets were stale — the exact "crash after applying flags"
report the North American users hit.

New behaviour: when stock is empty, fall back to the most-recent
bootstrapper install and MERGE our flags into whatever ClientAppSettings
the bootstrapper already wrote there. FFM's flags win conflicts; the
bootstrapper's other settings survive.
"""
import json
import os

import pytest

from src.core.roblox_manager import RobloxManager


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Provide two throwaway version dirs: one 'stock', one 'bootstrapper'.
    Tests decide which of these `get_writable_version_dirs` /
    `get_all_roblox_version_dirs` see."""
    stock_dir = tmp_path / "stock" / "version-abc"
    boot_dir = tmp_path / "boot" / "version-xyz"
    stock_dir.mkdir(parents=True)
    boot_dir.mkdir(parents=True)
    return {"stock": str(stock_dir), "boot": str(boot_dir)}


def _read_settings(vdir):
    p = os.path.join(vdir, "ClientSettings", "ClientAppSettings.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def test_falls_back_to_bootstrapper_when_stock_dirs_empty(isolated_dirs, monkeypatch):
    """No stock install exists → FFM must merge into the bootstrapper dir."""
    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs",
                        staticmethod(lambda: []))
    monkeypatch.setattr(RobloxManager, "get_all_roblox_version_dirs",
                        staticmethod(lambda: [isolated_dirs["boot"]]))

    flags = {"FFlagTestOne": "true", "DFIntChunkSize": "42"}
    ok, msg = RobloxManager.apply_fflags_json(flags)

    assert ok is True
    assert "bootstrapper install" in msg
    written = _read_settings(isolated_dirs["boot"])
    assert written["FFlagTestOne"] == "True"
    assert written["DFIntChunkSize"] == "42"


def test_bootstrapper_fallback_merges_existing_flags(isolated_dirs, monkeypatch):
    """User configured flags through the bootstrapper's own UI — that
    ClientAppSettings.json exists. FFM must PRESERVE the bootstrapper's
    entries and layer its own on top (FFM wins on key conflict)."""
    # Pre-seed the bootstrapper's own settings file
    settings_dir = os.path.join(isolated_dirs["boot"], "ClientSettings")
    os.makedirs(settings_dir)
    existing = {
        "FFlagBootstrapperOwn": "bloxstrap-value",
        "FFlagShared": "bootstrapper-wrote-this",   # will be overridden
    }
    with open(os.path.join(settings_dir, "ClientAppSettings.json"), "w") as f:
        json.dump(existing, f)

    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs",
                        staticmethod(lambda: []))
    monkeypatch.setattr(RobloxManager, "get_all_roblox_version_dirs",
                        staticmethod(lambda: [isolated_dirs["boot"]]))

    ffm_flags = {
        "FFlagFromFFM": "ffm-value",
        "FFlagShared": "ffm-wins",   # overrides bootstrapper's
    }
    ok, _ = RobloxManager.apply_fflags_json(ffm_flags)

    assert ok is True
    written = _read_settings(isolated_dirs["boot"])
    assert written["FFlagBootstrapperOwn"] == "bloxstrap-value", \
        "bootstrapper's own flags must survive the merge"
    assert written["FFlagFromFFM"] == "ffm-value", \
        "FFM's new flags must land"
    assert written["FFlagShared"] == "ffm-wins", \
        "FFM must win on key collision so live-memory + JSON stay consistent"


def test_stock_dir_still_wins_when_present(isolated_dirs, monkeypatch):
    """User has stock install AND a bootstrapper install. FFM must write to
    the stock dir only, NEVER the bootstrapper's (which manages its own)."""
    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs",
                        staticmethod(lambda: [isolated_dirs["stock"]]))
    monkeypatch.setattr(RobloxManager, "get_all_roblox_version_dirs",
                        staticmethod(lambda: [
                            isolated_dirs["stock"], isolated_dirs["boot"],
                        ]))

    flags = {"FFlagTest": "true"}
    ok, msg = RobloxManager.apply_fflags_json(flags)

    assert ok is True
    assert "bootstrapper install" not in msg
    # Stock got the flags
    assert _read_settings(isolated_dirs["stock"]) == {"FFlagTest": "True"}
    # Bootstrapper was left alone
    boot_settings = os.path.join(
        isolated_dirs["boot"], "ClientSettings", "ClientAppSettings.json",
    )
    assert not os.path.isfile(boot_settings), \
        "bootstrapper dir must not receive FFM's writes when stock exists"


def test_returns_false_when_no_dirs_exist_at_all(isolated_dirs, monkeypatch):
    """No stock install, no bootstrapper install — nothing to do. Return
    a clean error rather than crashing on some downstream path."""
    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs",
                        staticmethod(lambda: []))
    monkeypatch.setattr(RobloxManager, "get_all_roblox_version_dirs",
                        staticmethod(lambda: []))

    ok, msg = RobloxManager.apply_fflags_json({"FFlagX": "true"})

    assert ok is False
    assert "No Roblox version directories" in msg


def test_bootstrapper_merge_tolerates_corrupt_existing_json(isolated_dirs, monkeypatch):
    """The bootstrapper's existing settings file is malformed JSON. We must
    NOT crash; overwrite with FFM's payload rather than leaving Roblox with
    a broken settings file forever."""
    settings_dir = os.path.join(isolated_dirs["boot"], "ClientSettings")
    os.makedirs(settings_dir)
    with open(os.path.join(settings_dir, "ClientAppSettings.json"), "w") as f:
        f.write("{not valid json at all")

    monkeypatch.setattr(RobloxManager, "get_writable_version_dirs",
                        staticmethod(lambda: []))
    monkeypatch.setattr(RobloxManager, "get_all_roblox_version_dirs",
                        staticmethod(lambda: [isolated_dirs["boot"]]))

    ok, _ = RobloxManager.apply_fflags_json({"FFlagRecoveredOnly": "true"})

    assert ok is True
    assert _read_settings(isolated_dirs["boot"]) == {"FFlagRecoveredOnly": "True"}
