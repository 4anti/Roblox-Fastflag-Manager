"""Tests for Api.start_roblox_download.

The download decision now targets Roblox's LATEST production build (via the
Roblox CDN's ClientVersion endpoint) rather than whatever the offset dump
targets. Rationale:

  1. Roblox rejects clients too far behind. A stale offset mirror
     recommending an older build would leave the user unable to join.
  2. The apply-flow guard in flag_manager.apply_flags_hybrid handles the
     follow-up case where offsets don't yet match the freshly-installed
     build: live memory is skipped, JSON is written, no crash.

These tests are the regression fence against re-introducing either the
old "download whatever the offset dump says" behaviour OR the "refuse to
download until offsets are aligned" behaviour.
"""
import pytest

from src.core import offset_loader
from src.core.roblox_manager import RobloxManager
from src.core.version_changer import deployment, fixer
from src.gui.api import Api


@pytest.fixture
def api(monkeypatch):
    a = Api.__new__(Api)
    a.flag_manager = None
    a.roblox_manager = None
    a.settings = {}
    a._fix_state = "idle"
    a._fix_progress = 0
    a._fix_message = ""
    a._fix_cancel = False
    return a


def _install_noop_worker(monkeypatch):
    """Neuter the background worker so tests can inspect the initial state
    transition without touching disk or network."""
    import threading

    class _NoopThread:
        def __init__(self, *_a, **_k):
            pass
        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", _NoopThread)


def test_error_when_roblox_cdn_unreachable(api, monkeypatch):
    """When the Roblox CDN can't tell us what the latest build is, we can't
    honestly say the user is out of date. Return error, don't act."""
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-anything"))
    monkeypatch.setattr(deployment, "get_latest_production_guid", lambda: None)

    result = api.start_roblox_download()

    assert result["state"] == "error"
    assert "Roblox" in result["message"]
    assert api._fix_state == "idle"


def test_already_matching_when_installed_equals_latest(api, monkeypatch):
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-same"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-same")

    result = api.start_roblox_download()

    assert result["state"] == "already_matching"
    assert api._fix_state == "idle"


def test_worker_downloads_latest_production_not_offsets_target(api, monkeypatch, tmp_path):
    """The worker must call fixer.run_upgrade with the Roblox CDN's LATEST
    production build. Regression guard against the earlier 'sync to
    offsets_target' behaviour, which risked downgrading users to a build
    Roblox servers no longer accept."""
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-installedOLD"))
    monkeypatch.setattr(RobloxManager, "get_roblox_version_dir",
                        staticmethod(lambda: str(tmp_path / "version-installedOLD")))
    monkeypatch.setattr(RobloxManager, "get_all_roblox_version_dirs",
                        staticmethod(lambda: []))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-newLATEST")
    # Offset dump targets a DIFFERENT older build than roblox latest — the
    # download must ignore this and follow Roblox CDN's truth.
    monkeypatch.setattr(offset_loader, "fetch_latest_build",
                        lambda: "version-STALE_mirror")

    seen_targets: list[str] = []

    def _fake_run_upgrade(target_guid, versions_root, cache_dirs,
                          progress=None, should_cancel=None):
        seen_targets.append(target_guid)
        return {"ok": True, "state": "installed", "final_path": None,
                "message": "test-run"}

    monkeypatch.setattr(fixer, "run_upgrade", _fake_run_upgrade)

    import threading

    class _InlineThread:
        def __init__(self, target=None, *_a, **_k):
            self._target = target
        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(threading, "Thread", _InlineThread)

    result = api.start_roblox_download()

    assert result["state"] == "started"
    assert seen_targets == ["version-newLATEST"], (
        "Worker must download Roblox CDN's LATEST production build, NOT "
        f"whatever the offset dump reports. Got: {seen_targets!r}"
    )
    assert api._fix_state == "done"


def test_download_never_downgrades_when_installed_is_ahead_of_offsets(api, monkeypatch, tmp_path):
    """Fishstrap scenario: bootstrapper installed a NEWER Roblox than the
    offset dump targets. Old code would `blocked_downgrade` or (worse)
    downgrade to the stale mirror build. New code targets `latest_production`
    — if installed already equals latest, we're `already_matching` and DO
    NOT touch Roblox even though offsets are stale."""
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-newLATEST"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-newLATEST")
    monkeypatch.setattr(offset_loader, "fetch_latest_build",
                        lambda: "version-STALE_mirror")  # ignored

    result = api.start_roblox_download()

    assert result["state"] == "already_matching", (
        "Must NOT downgrade even when offsets target an older build — "
        "Roblox rejects clients too far behind. "
        f"Got: {result!r}"
    )
    assert api._fix_state == "idle"


def test_loading_status_exposes_new_source_health_fields(api, monkeypatch):
    """The frontend uses `roblox_is_latest`, `offsets_current`, and
    `source_healthy` to paint the six-row version-checker card. Verify each
    field mirrors the honest signal from the loader/deployment layers."""
    class _FM:
        offsets_loaded = True
        offsets_loading = False
        preset_flags_list = []
    api.flag_manager = _FM()
    api._init_error = None
    api.settings = {}

    monkeypatch.setattr(offset_loader, "last_source_id", lambda: "imtheo_requests")
    monkeypatch.setattr(offset_loader, "is_baseline_stale", lambda: False)
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-90f2fddd3b244ff6")
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-90f2fddd3b244ff6"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-90f2fddd3b244ff6")

    status = api.get_loading_status()

    assert status["roblox_is_latest"] is True
    assert status["offsets_current"] is True
    assert status["source_healthy"] is True
    assert status["latest_production"] == "version-90f2fddd3b244ff6"


def test_loading_status_flags_source_unhealthy_when_on_github_fallback(api, monkeypatch):
    class _FM:
        offsets_loaded = True
        offsets_loading = False
        preset_flags_list = []
    api.flag_manager = _FM()
    api._init_error = None
    api.settings = {}

    monkeypatch.setattr(offset_loader, "last_source_id", lambda: "github_requests")
    monkeypatch.setattr(offset_loader, "is_baseline_stale", lambda: False)
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-STALE")
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-CURRENT"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-CURRENT")

    status = api.get_loading_status()

    assert status["source_healthy"] is False   # on github mirror
    assert status["offsets_current"] is False  # mirror is stale
    assert status["roblox_is_latest"] is True  # user's roblox is fine
