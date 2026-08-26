"""Protocol join must write ClientAppSettings.json AFTER any version upgrade
so a newly created version folder is not launched empty."""
from src.core import bootstrap_launch
from src.core import roblox_manager as rm_mod
from src.core.version_changer import deployment, fastpath, fixer


def _patch_join(monkeypatch, order, launched, *, installed="version-old",
                latest="version-new", up_to_date=False, upgrade_ok=True,
                roblox_running=False):
    class FakeRM:
        def __init__(self, pid=None):
            pass

        @staticmethod
        def get_roblox_version_string():
            return installed

        @staticmethod
        def get_install_versions_root():
            return r"C:\versions"

        @staticmethod
        def resolve_download_versions_root():
            return r"C:\versions"

        @staticmethod
        def get_all_roblox_version_dirs():
            return []

        @staticmethod
        def is_roblox_running():
            return roblox_running

        def launch_specific_version(self, version, args=None):
            order.append("launch")
            launched.append(version)
            return True, None

        @staticmethod
        def mark_startup_write():
            pass

    monkeypatch.setattr(rm_mod, "RobloxManager", FakeRM)
    monkeypatch.setattr(bootstrap_launch, "write_startup_flags",
                        lambda guid=None: order.append("flags") or True)
    monkeypatch.setattr(bootstrap_launch, "_restore_third_party_if_transient",
                        lambda: None)
    monkeypatch.setattr(fastpath, "is_up_to_date", lambda _inst: up_to_date)
    monkeypatch.setattr(deployment, "get_latest_production_guid", lambda: latest)

    def fake_upgrade(*_a, **_k):
        order.append("upgrade")
        return {"ok": upgrade_ok, "state": "installed"}

    monkeypatch.setattr(fixer, "run_upgrade", fake_upgrade)
    monkeypatch.setattr(
        fixer, "prune_stock_non_production",
        lambda g: order.append("prune") or {"removed": [], "failed": [], "kept": None},
    )


def test_bootstrap_join_writes_flags_after_upgrade(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False)
    bootstrap_launch.bootstrap_join("roblox://join")
    assert order == ["upgrade", "prune", "flags", "launch"]
    assert launched == ["version-new"]


def test_launch_join_writes_flags_after_upgrade(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False)
    bootstrap_launch.launch_join("roblox://join")
    assert order == ["upgrade", "prune", "flags", "launch"]
    assert launched == ["version-new"]


def test_fast_join_writes_flags_before_launch_without_upgrade(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, installed="version-new",
                latest="version-new", up_to_date=True)
    bootstrap_launch.bootstrap_join("roblox://join")
    assert order == ["prune", "flags", "launch"]
    assert "upgrade" not in order
    assert launched == ["version-new"]


def test_join_upgrades_when_no_install(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, installed="unknown",
                latest="version-new", up_to_date=False)
    bootstrap_launch.bootstrap_join("roblox://join")
    assert order == ["upgrade", "prune", "flags", "launch"]
    assert launched == ["version-new"]


def test_join_skips_prune_when_roblox_running(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False, roblox_running=True)
    bootstrap_launch.launch_join("roblox://join")
    assert "prune" not in order
    assert launched == ["version-new"]


def test_join_does_not_launch_when_flag_write_fails(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False)
    monkeypatch.setattr(bootstrap_launch, "write_startup_flags",
                        lambda guid=None: order.append("flags") or False)
    bootstrap_launch.launch_join("roblox://join")
    assert order.count("flags") == 2
    assert "launch" not in order
    assert launched == []


def test_fast_join_does_not_launch_when_flag_write_fails(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, installed="version-new",
                latest="version-new", up_to_date=True)
    monkeypatch.setattr(bootstrap_launch, "write_startup_flags",
                        lambda guid=None: order.append("flags") or False)
    bootstrap_launch.bootstrap_join("roblox://join")
    assert order.count("flags") >= 2
    assert "launch" not in order
    assert launched == []


def test_join_retries_write_then_launches(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False)
    state = {"n": 0}

    def flaky(guid=None):
        order.append("flags")
        state["n"] += 1
        return state["n"] >= 2

    monkeypatch.setattr(bootstrap_launch, "write_startup_flags", flaky)
    bootstrap_launch.launch_join("roblox://join")
    assert order.count("flags") == 2
    assert order[-1] == "launch"
    assert launched == ["version-new"]


def test_join_does_not_launch_when_flag_write_fails(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False)
    monkeypatch.setattr(bootstrap_launch, "write_startup_flags",
                        lambda guid=None: order.append("flags") or False)
    bootstrap_launch.launch_join("roblox://join")
    assert "launch" not in order
    assert launched == []
    assert order.count("flags") == 2


def test_join_retries_write_then_launches(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, up_to_date=False)
    n = {"c": 0}

    def flaky(guid=None):
        order.append("flags")
        n["c"] += 1
        return n["c"] >= 2

    monkeypatch.setattr(bootstrap_launch, "write_startup_flags", flaky)
    bootstrap_launch.launch_join("roblox://join")
    assert order == ["upgrade", "prune", "flags", "flags", "launch"]
    assert launched == ["version-new"]


def test_fast_join_does_not_launch_when_flag_write_fails(monkeypatch):
    order = []
    launched = []
    _patch_join(monkeypatch, order, launched, installed="version-new",
                latest="version-new", up_to_date=True)
    monkeypatch.setattr(bootstrap_launch, "write_startup_flags",
                        lambda guid=None: order.append("flags") or False)
    bootstrap_launch.launch_join("roblox://join")
    assert "launch" not in order
    assert "upgrade" not in order
    assert launched == []
    assert order.count("flags") >= 2
