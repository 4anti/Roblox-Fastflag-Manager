"""v3.3.8 -> v4 update path: update detection + settings migration.

Verifies that the (identical) v3.3.8 updater logic detects v4 and finds the
installer asset, and that v4 reads a v3.3.8 settings.json (signed with the old
build's rotated HMAC key) without breaking — re-signing transparently.
"""
import json

from src.utils import updater
from src.utils.config import Config, _hmac_fingerprint


# ---- Update detection (mirrors what v3.3.8's updater does) ----

class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_v4_release_is_detected_from_338(monkeypatch):
    payload = {
        "tag_name": "v4.0.0",
        "body": "v4 changelog",
        "assets": [
            {"name": "FFM_Installer.exe",
             "browser_download_url": "https://github.com/x/FFM_Installer.exe"},
        ],
    }
    monkeypatch.setattr(updater, "get_current_version", lambda: "3.3.8")
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _Resp(payload))

    has_update, exe_url, version, changelog = updater.check_for_updates()
    assert has_update is True
    assert exe_url.endswith("FFM_Installer.exe")
    assert version == "4.0.0"
    assert changelog == "v4 changelog"


def test_no_update_when_already_v4(monkeypatch):
    payload = {"tag_name": "v4.0.0", "body": "", "assets": []}
    monkeypatch.setattr(updater, "get_current_version", lambda: "4.0.0")
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _Resp(payload))
    has_update, *_ = updater.check_for_updates()
    assert has_update is False


def test_missing_installer_asset_is_reported_not_crashed(monkeypatch):
    payload = {"tag_name": "v4.0.0", "body": "", "assets": [
        {"name": "source.zip", "browser_download_url": "https://x/source.zip"}]}
    monkeypatch.setattr(updater, "get_current_version", lambda: "3.3.8")
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _Resp(payload))
    has_update, exe_url, version, _ = updater.check_for_updates()
    assert has_update is True and exe_url is None and version == "4.0.0"


# ---- Settings migration (rotated HMAC key from the old build) ----

def test_v338_settings_migrate_without_breaking(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "APP_DIR", tmp_path)
    monkeypatch.setattr(Config, "SETTINGS_FILE", tmp_path / "settings.json")

    old_settings = {
        "auto_apply": True,
        "history_limit": 50,
        "ui_theme": "matrix",
        "_integrity": "deadbeef" * 8,
        "_key_fp": "OLD_BUILD_FP",  # a different build's fingerprint
    }
    (tmp_path / "settings.json").write_text(json.dumps(old_settings), encoding="utf-8")

    loaded = Config.load_settings()

    # The user's old settings survive.
    assert loaded["auto_apply"] is True
    assert loaded["history_limit"] == 50
    assert loaded["ui_theme"] == "matrix"
    # New v4 default keys are merged in.
    assert "enforcement_mode" in loaded
    # On disk, it was transparently re-signed with the current build's key.
    disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert disk["_key_fp"] == _hmac_fingerprint()
    assert disk["auto_apply"] is True


def test_corrupt_settings_fall_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "APP_DIR", tmp_path)
    monkeypatch.setattr(Config, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text("{ this is not json", encoding="utf-8")
    loaded = Config.load_settings()
    # Corrupt file -> fall back to the built-in defaults, no crash.
    assert loaded == Config.DEFAULT_SETTINGS
    assert "enforcement_mode" in loaded
