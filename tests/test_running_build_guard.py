"""Regression guard for the running-vs-installed build fix.

The pre-2026-07 version-mismatch check compared offsets against
``RobloxManager.get_roblox_version_string()`` — the DISK-NEWEST build
directory. That value drifts ahead of the ATTACHED PID whenever a
bootstrapper writes a fresh ``version-YYY/`` while the older build is
still running: the running PID is on build X, disk-latest says Y, the
check compares Y == Y, passes, and the following WriteProcessMemory
calls hit wrong RVAs and crash Roblox.

The fix reads the running PID's own on-disk exe path via
``QueryFullProcessImageNameW`` (see
``RobloxManager.get_running_build_string``) and feeds THAT into
``fixer.is_version_mismatch``. These tests lock in that behaviour.
"""

from __future__ import annotations

import threading

import pytest

from src.core import offset_loader
from src.core.flag_manager import FlagManager


class _StubRoblox:
    """Attached RobloxManager stub. Records memory-path calls; exposes a
    settable ``running_build`` for the guard to observe."""

    is_attached = True

    def __init__(self, running_build: str = "unknown"):
        self.running_build = running_build
        self.memory_calls = 0

    def open_process_for_write(self):
        self.memory_calls += 1
        return True

    def get_roblox_base(self):
        self.memory_calls += 1
        return 0x140000000

    def scan_live_flags(self, *_a, **_k):
        self.memory_calls += 1
        return {}

    def get_running_build_string(self):
        return self.running_build

    # The disk-latest getter DELIBERATELY returns the wrong build here so the
    # regression is loud: if the guard ever falls back to it, tests fail.
    @staticmethod
    def get_roblox_version_string():
        return "version-DISK-latest-would-hide-the-bug"

    @staticmethod
    def apply_fflags_json(_flags_dict):
        return True, "stubbed json write"


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
    m._rm = None
    m._lock = threading.Lock()
    return m


def test_guard_reads_running_pid_build_not_disk(fm, monkeypatch):
    """Disk-latest agrees with offsets; RUNNING PID is behind. The guard
    MUST fire — the previous, disk-based check would have missed this."""
    from src.core.roblox_manager import RobloxManager

    monkeypatch.setattr(RobloxManager, "apply_fflags_json",
                        staticmethod(_StubRoblox.apply_fflags_json))
    # Disk-latest matches offsets — a disk-based guard would say "aligned".
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "version-latest-aligned"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-latest-aligned")

    stub = _StubRoblox(running_build="version-BEHIND")
    fm.apply_flags_hybrid(stub, skip_json=False)

    assert stub.memory_calls == 0, (
        "The guard must consult the ATTACHED PID's build, not the disk-latest. "
        f"memory_calls={stub.memory_calls} — the guard was fooled by the disk "
        "value and let a live-memory write onto a mismatched build through."
    )
    assert fm.flags_applied is True   # JSON path succeeded


def test_guard_stays_off_when_running_matches_offsets(fm, monkeypatch):
    """Sanity: running-build matches offsets → live memory runs normally."""
    from src.core.roblox_manager import RobloxManager

    monkeypatch.setattr(RobloxManager, "apply_fflags_json",
                        staticmethod(_StubRoblox.apply_fflags_json))
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "unrelated"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-aligned")

    stub = _StubRoblox(running_build="version-aligned")
    fm.apply_flags_hybrid(stub, skip_json=False)

    assert stub.memory_calls >= 1, (
        "Live memory step must run when the running PID matches the loaded "
        "offsets — the guard misfired."
    )


def test_guard_treats_unknown_running_as_no_signal(fm, monkeypatch):
    """`is_version_mismatch("unknown", X)` returns False by contract. A
    QueryFullProcessImageNameW failure or a detached PID must NEVER cause
    the guard to block a legitimate apply."""
    from src.core.roblox_manager import RobloxManager

    monkeypatch.setattr(RobloxManager, "apply_fflags_json",
                        staticmethod(_StubRoblox.apply_fflags_json))
    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "unrelated"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-something")

    stub = _StubRoblox(running_build="unknown")
    fm.apply_flags_hybrid(stub, skip_json=False)

    assert stub.memory_calls >= 1, (
        "Guard must not block on an unknown running-build reading — that "
        "signal is ambiguous and blocking would nuke headless / edge cases."
    )


def test_helper_is_no_op_when_rm_missing(fm):
    """`_live_writes_gated(None)` returns (False, "") — a caller with no RM
    to interrogate MUST fall through to normal behaviour."""
    gated, reason = fm._live_writes_gated(None)
    assert gated is False and reason == ""


def test_helper_falls_back_to_self_rm_when_arg_omitted(fm, monkeypatch):
    """Hotkey loop calls `_live_writes_gated()` with no arg — the helper
    must transparently use ``self._rm``."""
    from src.core.roblox_manager import RobloxManager

    monkeypatch.setattr(RobloxManager, "get_roblox_version_string",
                        staticmethod(lambda: "unrelated"))
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: "version-A")

    fm._rm = _StubRoblox(running_build="version-B")
    gated, reason = fm._live_writes_gated()
    assert gated is True
    assert "version-A" in reason and "version-B" in reason
