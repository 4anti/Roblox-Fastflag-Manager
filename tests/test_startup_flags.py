"""Startup JSON write: string values, launch-folder verify, idle-clear skip."""
import json
import os

from src.core import bootstrap_launch
from src.core.roblox_manager import RobloxManager
from src.gui.api import Api
from src.utils.config import Config


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _isolate_app(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(Config, "APP_DIR", app)
    monkeypatch.setattr(Config, "USER_FLAGS_FILE", app / "user_flags.json")
    monkeypatch.setattr(Config, "SETTINGS_FILE", app / "settings.json")
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return app, local


def _mk_player(local, guid="version-launch"):
    vdir = local / "Roblox" / "Versions" / guid
    vdir.mkdir(parents=True)
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    return vdir


def test_apply_fflags_json_writes_bools_as_strings(tmp_path, monkeypatch):
    app, local = _isolate_app(tmp_path, monkeypatch)
    vdir = _mk_player(local, "version-abc")
    ok, _ = RobloxManager.apply_fflags_json({"FFlagDebugDisplayFPS": True})
    assert ok is True
    data = _read(vdir / "ClientSettings" / "ClientAppSettings.json")
    assert data["FFlagDebugDisplayFPS"] == "True"
    assert isinstance(data["FFlagDebugDisplayFPS"], str)


def test_prefer_guid_writes_launch_folder(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    local = tmp_path / "Local"
    vdir = local / "Roblox" / "Versions" / "version-new"
    vdir.mkdir(parents=True)
    ok, _ = RobloxManager.apply_fflags_json(
        {"FFlagDebugDisplayFPS": False}, prefer_guid="version-new",
    )
    assert ok is True
    data = _read(vdir / "ClientSettings" / "ClientAppSettings.json")
    assert data["FFlagDebugDisplayFPS"] == "False"
    assert isinstance(data["FFlagDebugDisplayFPS"], str)


def test_write_startup_flags_round_trips_string_in_launch_folder(tmp_path, monkeypatch):
    app, local = _isolate_app(tmp_path, monkeypatch)
    vdir = _mk_player(local, "version-launch")
    Config.USER_FLAGS_FILE.write_text(json.dumps([
        {
            "name": "FFlagDebugDisplayFPS",
            "value": "True",
            "type": "bool",
            "enabled": True,
        }
    ]), encoding="utf-8")
    ok = bootstrap_launch.write_startup_flags("version-launch")
    assert ok is True
    data = _read(vdir / "ClientSettings" / "ClientAppSettings.json")
    assert "FFlagDebugDisplayFPS" in data
    assert isinstance(data["FFlagDebugDisplayFPS"], str)
    assert data["FFlagDebugDisplayFPS"].lower() == "true"


def test_write_startup_flags_prefixes_bare_bool_name(tmp_path, monkeypatch):
    app, local = _isolate_app(tmp_path, monkeypatch)
    vdir = _mk_player(local, "version-launch")
    Config.USER_FLAGS_FILE.write_text(json.dumps([
        {
            "name": "DebugDisplayFPS",
            "value": "True",
            "type": "bool",
            "enabled": True,
        }
    ]), encoding="utf-8")
    ok = bootstrap_launch.write_startup_flags("version-launch")
    assert ok is True
    data = _read(vdir / "ClientSettings" / "ClientAppSettings.json")
    assert "FFlagDebugDisplayFPS" in data
    assert isinstance(data["FFlagDebugDisplayFPS"], str)
    assert data["FFlagDebugDisplayFPS"].lower() == "true"
    assert "DebugDisplayFPS" not in data or "FFlagDebugDisplayFPS" in data


def test_write_startup_flags_false_when_launch_folder_missing_key(tmp_path, monkeypatch):
    app, local = _isolate_app(tmp_path, monkeypatch)
    vdir = _mk_player(local, "version-launch")
    Config.USER_FLAGS_FILE.write_text(json.dumps([
        {
            "name": "FFlagDebugDisplayFPS",
            "value": "True",
            "type": "bool",
            "enabled": True,
        }
    ]), encoding="utf-8")

    def drop_key(flags_dict, prefer_guid=None):
        (vdir / "ClientSettings").mkdir(exist_ok=True)
        path = vdir / "ClientSettings" / "ClientAppSettings.json"
        path.write_text("{}", encoding="utf-8")
        return True, "wrote empty"

    monkeypatch.setattr(RobloxManager, "apply_fflags_json", staticmethod(drop_key))
    assert bootstrap_launch.write_startup_flags("version-launch") is False


def test_idle_clear_skips_during_startup_write(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    RobloxManager.mark_startup_write()
    assert RobloxManager.startup_write_in_progress() is True

    cleared = []
    api = Api.__new__(Api)
    api.flag_manager = object()
    api.settings = {"auto_apply": False, "auto_clear_json": True}

    class _RM:
        def find_roblox_process(self):
            return None

    api.roblox_manager = _RM()
    api.clear_clientapp_json = lambda: cleared.append(True)
    monkeypatch.setattr(RobloxManager, "clientapp_json_has_flags",
                        staticmethod(lambda: True))
    api._reconcile_idle_clear()
    assert cleared == []


def test_idle_clear_still_runs_when_startup_write_expired(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    path = RobloxManager._startup_write_lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("1")  # 1970, well past TTL

    cleared = []
    api = Api.__new__(Api)
    api.flag_manager = object()
    api.settings = {"auto_apply": False, "auto_clear_json": True}

    class _RM:
        def find_roblox_process(self):
            return None

    api.roblox_manager = _RM()
    api.clear_clientapp_json = lambda: cleared.append(True)
    monkeypatch.setattr(RobloxManager, "clientapp_json_has_flags",
                        staticmethod(lambda: True))
    api._reconcile_idle_clear()
    assert cleared == [True]
