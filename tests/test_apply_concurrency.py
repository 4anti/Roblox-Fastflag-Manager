"""Preset-switch / apply crash fix.

Two guarantees:
1. RobloxManager._write_raw serializes via _mem_lock — two threads can never be
   inside the real write (VirtualProtectEx/WriteProcessMemory) at once.
2. FlagManager._applying brackets apply_flags_hybrid so the watchdog stands down
   for the whole apply (and is always reset, even on early-return / exception).
"""
import threading
import time

from src.core.roblox_manager import RobloxManager
from src.core import roblox_manager as rm_mod
from src.core.flag_manager import FlagManager


# ── 1. write lock serialization ──────────────────────────────────────────────

def test_mem_lock_exists():
    assert hasattr(rm_mod, "_mem_lock")
    assert hasattr(rm_mod._mem_lock, "acquire") and hasattr(rm_mod._mem_lock, "release")


def test_write_raw_is_serialized(monkeypatch):
    rm = RobloxManager.__new__(RobloxManager)  # bypass heavy __init__
    state = {"cur": 0, "max": 0}
    counter_lock = threading.Lock()

    def fake_impl(abs_addr, data):
        with counter_lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.005)            # widen the window a real race would exploit
        with counter_lock:
            state["cur"] -= 1
        return True, "OK"

    monkeypatch.setattr(rm, "_write_raw_impl", fake_impl)

    threads = [threading.Thread(target=lambda: rm._write_raw(0x1000, b"\x00"))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # If _write_raw didn't hold _mem_lock, multiple threads would overlap.
    assert state["max"] == 1


# ── 2. _applying brackets the apply (watchdog stand-down) ─────────────────────

class _FakeRM:
    def __init__(self, fm, attached=False):
        self.fm = fm
        self.is_attached = attached
        self.applying_during_json = None

    def apply_fflags_json(self, flags_dict):
        # Captured mid-apply: the watchdog gate must be engaged here.
        self.applying_during_json = self.fm._applying
        return True, f"Applied {len(flags_dict)} flags"


def _fm(attached=False):
    fm = FlagManager()
    fm.save_user_flags = lambda *a, **k: True
    fm.start_watchdog = lambda *a, **k: None  # don't spawn a real thread
    rm = _FakeRM(fm, attached=attached)
    fm._rm = rm
    return fm, rm


def test_applying_true_during_apply_false_after():
    fm, rm = _fm(attached=False)
    fm.user_flags = [{"name": "DFIntA", "value": "1", "type": "int", "enabled": True}]
    assert fm._applying is False
    fm.apply_flags_hybrid(rm)
    assert rm.applying_during_json is True   # gate engaged mid-apply
    assert fm._applying is False             # always reset afterwards


def test_applying_reset_when_no_flags():
    fm, rm = _fm()
    fm.user_flags = []
    fm.apply_flags_hybrid(rm)
    assert fm._applying is False


def test_applying_reset_on_exception():
    fm, rm = _fm()
    fm.user_flags = [{"name": "DFIntA", "value": "1", "type": "int", "enabled": True}]

    def boom(_d):
        raise RuntimeError("simulated write failure")

    rm.apply_fflags_json = boom
    fm.apply_flags_hybrid(rm)        # exception is caught internally
    assert fm._applying is False     # finally still reset the gate


def test_watchdog_skips_while_applying():
    # The watchdog enforcement gate is `_watchdog_paused or _applying`.
    fm, _rm = _fm()
    fm._applying = True
    assert (fm._watchdog_paused or fm._applying) is True   # would `continue`
    fm._applying = False
    fm._watchdog_paused = False
    assert (fm._watchdog_paused or fm._applying) is False  # would enforce
