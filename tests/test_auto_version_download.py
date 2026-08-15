"""Regression guard for the auto-download trigger inside _auto_version_once.

Every ~5 minutes the background version loop checks whether the installed
Roblox build lags Roblox's latest production build and, if so, kicks off
`start_roblox_download` in the background — no user prompt, no UI. These
tests fence the trigger behaviour so future edits don't silently drop the
auto-update path.
"""
import pytest

from src.core.roblox_manager import RobloxManager
from src.core.version_changer import deployment
from src.gui.api import Api


@pytest.fixture
def api(monkeypatch):
    a = Api.__new__(Api)
    a.flag_manager = None
    a.roblox_manager = None
    a.settings = {"bootstrapper_enabled": False, "_rbx_handler_backup": None}
    a._fix_state = "idle"
    a._fix_progress = 0
    a._fix_message = ""
    a._fix_cancel = False
    # Every downstream branch of _auto_version_once we don't care about is
    # stubbed to a no-op so the test isolates the auto-download decision.
    a._auto_last_registered = 0.0
    return a


def _stub_common(monkeypatch):
    """Stub out the offset-refresh + bootstrapper-register side effects so
    _auto_version_once's non-download branches don't touch real state."""
    import src.gui.api as api_module
    monkeypatch.setattr(api_module, "get_current_version", lambda: "test")
    # fastpath is invoked at the end of _auto_version_once — neuter it.
    from src.core.version_changer import fastpath, fixer
    monkeypatch.setattr(fastpath, "write_known_good", lambda *_a, **_k: None)
    # Never delete real Roblox folders from a unit test.
    monkeypatch.setattr(fixer, "prune_stock_non_production",
                        lambda *_a, **_k: {"removed": [], "failed": [], "kept": None})
    monkeypatch.setattr(RobloxManager, "is_roblox_running", staticmethod(lambda: False))
    # Stub offset_loader.fetch_latest_build so the offset-refresh branch
    # doesn't do network work either.
    from src.core import offset_loader
    monkeypatch.setattr(offset_loader, "fetch_latest_build", lambda: None)


def test_auto_version_triggers_download_when_installed_lags_latest(api, monkeypatch):
    """Installed build != latest_production, Roblox not running, no fix
    already in flight → the auto tick MUST spawn a download worker."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-installedOLD"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-newLATEST")

    called = {"n": 0, "state_before_call": None}

    def _spy():
        called["n"] += 1
        called["state_before_call"] = api._fix_state
        return {"state": "started", "message": "spy"}

    monkeypatch.setattr(api, "start_roblox_download", _spy)

    api._auto_version_once()

    assert called["n"] == 1, "Expected start_roblox_download to fire exactly once"


def test_auto_version_skips_download_when_installed_equals_latest(api, monkeypatch):
    """Installed already matches latest_production → no download, but leftover
    folders are still cleared."""
    _stub_common(monkeypatch)
    from src.core.version_changer import fixer
    pruned = []
    monkeypatch.setattr(fixer, "prune_stock_non_production",
                        lambda g: pruned.append(g) or {"removed": [], "failed": [], "kept": None})
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-same"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-same")

    called = {"n": 0}
    monkeypatch.setattr(api, "start_roblox_download",
                        lambda: (called.__setitem__("n", called["n"] + 1),
                                 {"state": "started"})[1])

    api._auto_version_once()

    assert called["n"] == 0
    assert pruned == ["version-same"]


def test_auto_version_skips_download_when_roblox_is_running(api, monkeypatch):
    """Never yank Roblox out from under the user — if the process is live,
    defer to the next tick."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-installedOLD"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-newLATEST")

    class _RunningRoblox:
        def find_roblox_process(self):
            return 12345   # non-zero pid = running

    api.roblox_manager = _RunningRoblox()

    called = {"n": 0}
    monkeypatch.setattr(api, "start_roblox_download",
                        lambda: (called.__setitem__("n", called["n"] + 1),
                                 {"state": "started"})[1])

    api._auto_version_once()

    assert called["n"] == 0, (
        "Auto-download must defer when Roblox is running; "
        f"got {called['n']} start_roblox_download calls."
    )


def test_auto_version_does_not_prune_when_roblox_is_running(api, monkeypatch):
    _stub_common(monkeypatch)
    from src.core.version_changer import fixer
    pruned = []
    monkeypatch.setattr(fixer, "prune_stock_non_production",
                        lambda g: pruned.append(g) or {"removed": [], "failed": [], "kept": None})
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-same"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-same")

    class _RunningRoblox:
        def find_roblox_process(self):
            return 12345

    api.roblox_manager = _RunningRoblox()
    api._auto_version_once()
    assert pruned == []


def test_auto_version_skips_download_when_fix_already_running(api, monkeypatch):
    """A worker is already in flight — don't stack a second one on top."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-installedOLD"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-newLATEST")
    api._fix_state = "running"

    called = {"n": 0}
    monkeypatch.setattr(api, "start_roblox_download",
                        lambda: (called.__setitem__("n", called["n"] + 1),
                                 {"state": "started"})[1])

    api._auto_version_once()

    assert called["n"] == 0


def test_auto_version_skips_download_when_cdn_unreachable(api, monkeypatch):
    """No latest_production known → no honest target → don't act."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-installedOLD"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: None)

    called = {"n": 0}
    monkeypatch.setattr(api, "start_roblox_download",
                        lambda: (called.__setitem__("n", called["n"] + 1),
                                 {"state": "started"})[1])

    api._auto_version_once()

    assert called["n"] == 0


def test_auto_version_triggers_download_when_no_roblox_install(api, monkeypatch):
    """No version folders (unknown install) must still kick off a production
    download so a wiped Roblox tree can be restored from scratch."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "unknown"))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-newLATEST")

    called = {"n": 0}
    monkeypatch.setattr(api, "start_roblox_download",
                        lambda: (called.__setitem__("n", called["n"] + 1),
                                 {"state": "started"})[1])

    api._auto_version_once()

    assert called["n"] == 1
