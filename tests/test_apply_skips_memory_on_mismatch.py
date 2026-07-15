"""Regression guard for the version-mismatch apply guard.

Before this fix, `apply_flags_hybrid` proceeded to Step 2 (live memory
writes) even when the loaded offsets targeted a different Roblox build than
the running process — the loader logged `[!] VERSION MISMATCH ... may fail
or crash` and the apply engine wrote to wrong RVAs anyway, crashing Roblox
on the next frame. This test asserts the guard now short-circuits Step 2
when a mismatch is detected, keeping JSON writes intact.
"""
import time

import pytest

from src.core import flag_manager as flag_manager_module
from src.core import offset_loader
from src.core.flag_manager import FlagManager
from src.core.roblox_manager import RobloxManager
from src.core.version_changer import fixer as vc_fixer


class _StubRoblox:
    """Fake attached RobloxManager. Records whether scan/open ever ran."""
    is_attached = True
    memory_calls = 0
    # Overridden per-test via monkeypatch on the instance; default matches
    # "unknown" so the guard treats it as no-signal by default.
    running_build = "unknown"

    def open_process_for_write(self):
        _StubRoblox.memory_calls += 1
        return True

    def get_roblox_base(self):
        _StubRoblox.memory_calls += 1
        return 0x140000000

    @staticmethod
    def apply_fflags_json(_flags_dict):
        return True, "stubbed json write"

    def scan_live_flags(self, *_a, **_k):
        _StubRoblox.memory_calls += 1
        return {}

    def get_running_build_string(self):
        return self.running_build


@pytest.fixture(autouse=True)
def _reset_stub():
    _StubRoblox.memory_calls = 0


@pytest.fixture
def fm():
    m = FlagManager.__new__(FlagManager)
    m.user_flags = [
        {"name": "FFlagTest", "value": "true", "type": "bool", "enabled": True},
    ]
    m.official_types = {}
    m.official_prefixes = {}
    m.preset_flags_list = []
    m.offsets_loaded = True
    m.offsets_loading = False
    m.flags_applied = False
    m.last_apply_time = 0
    m._applying = False
    import threading
    m._lock = threading.Lock()
    return m


def test_apply_skips_memory_when_offsets_target_different_build(fm, monkeypatch):
    """Roblox running is `version-90f2fdd...`, offsets loaded target
    `version-ad5d3e...`. Live memory writes MUST NOT run — RVAs are for the
    wrong build and would crash the process."""
    monkeypatch.setattr(RobloxManager, "apply_fflags_json",
                        staticmethod(_StubRoblox.apply_fflags_json))
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-90f2fddd3b244ff6"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-ad5d3e2906444472")
    # Real is_version_mismatch will return True for these — no need to stub.

    stub = _StubRoblox()
    stub.running_build = "version-90f2fddd3b244ff6"
    fm.apply_flags_hybrid(stub, skip_json=False)

    assert _StubRoblox.memory_calls == 0, (
        "apply_flags_hybrid must skip Step 2 memory writes when the loaded "
        "offsets target a different Roblox build than the running process. "
        f"Recorded {_StubRoblox.memory_calls} memory-path calls."
    )
    assert fm.flags_applied is True   # JSON path succeeded
    assert fm.last_apply_time > 0


def test_apply_runs_memory_when_offsets_target_matches_running_build(fm, monkeypatch):
    """Sanity: when offsets are aligned, Step 2 must run normally so live
    flags actually reach the running Roblox process."""
    monkeypatch.setattr(RobloxManager, "apply_fflags_json",
                        staticmethod(_StubRoblox.apply_fflags_json))
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-aligned"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-aligned")

    stub = _StubRoblox()
    stub.running_build = "version-aligned"
    fm.apply_flags_hybrid(stub, skip_json=False)

    # At LEAST open_process_for_write / get_roblox_base / scan_live_flags
    # should have been visited before the loop early-exits on empty results.
    assert _StubRoblox.memory_calls >= 1, (
        "Step 2 must run when offsets align — got zero memory calls, "
        "which suggests the guard misfired."
    )


def test_apply_guard_never_blocks_when_installed_unknown(fm, monkeypatch):
    """`is_version_mismatch(None, X)` returns False by contract — a missing
    installed build must NEVER cause the guard to swallow legitimate applies.
    Preserves behaviour for headless test scenarios where no Roblox exists."""
    monkeypatch.setattr(RobloxManager, "apply_fflags_json",
                        staticmethod(_StubRoblox.apply_fflags_json))
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "unknown"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-something")

    stub = _StubRoblox()
    stub.running_build = "unknown"
    fm.apply_flags_hybrid(stub, skip_json=False)

    # is_version_mismatch("unknown", "version-something") == False → guard doesn't trip.
    assert _StubRoblox.memory_calls >= 1
