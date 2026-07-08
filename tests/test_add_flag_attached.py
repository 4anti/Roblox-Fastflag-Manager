"""Regression: adding a flag while Roblox is attached must not silently fail.

get_live_flag_address returns a LIST of address entries; add_flag previously
indexed it as a dict (addr_data['abs_addr']), raising TypeError whenever Roblox
was attached and the flag was mappable — so adds "didn't work" in-game."""
import threading

from src.gui.api import Api


class _FM:
    def __init__(self):
        self._lock = threading.Lock()
        self.user_flags = []
        self.official_types = {}

    def save_history_snapshot(self, action, limit):
        pass

    def save_user_flags(self):
        pass


def _make_api(roblox_manager):
    api = Api.__new__(Api)  # bypass the heavy __init__
    api.flag_manager = _FM()
    api.roblox_manager = roblox_manager
    api.settings = {"history_limit": 20, "auto_apply": False}
    return api


class _RM:
    is_attached = True

    def __init__(self, addr_data):
        self._addr_data = addr_data

    def get_live_flag_address(self, name):
        return self._addr_data

    def read_flag_at_address(self, ftype, addr):
        return "60"


def test_add_flag_succeeds_when_attached_with_list_address():
    api = _make_api(_RM([{"abs_addr": 0x1234, "type": "int"}]))
    result = api.add_flag("DFIntTestFlag", "120")
    assert result == {"ok": True}
    assert len(api.flag_manager.user_flags) == 1
    assert api.flag_manager.user_flags[0]["original_value"] == "60"


def test_add_flag_not_blocked_when_capture_raises():
    class _BadRM:
        is_attached = True

        def get_live_flag_address(self, name):
            raise RuntimeError("boom")

    api = _make_api(_BadRM())
    result = api.add_flag("DFIntTestFlag", "120")
    assert result == {"ok": True}
    assert len(api.flag_manager.user_flags) == 1
