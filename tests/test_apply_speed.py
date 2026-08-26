"""Guaranteed apply speed-ups: keep JSON when Auto Apply is on, faster PID
poll, Join does not sleep 1s after memory is readable, skip a second JSON write.
"""
from src.core.flag_manager import (
    FlagManager,
    LAUNCH_INIT_POLL_SEC,
)
from src.core.roblox_manager import RobloxManager
from src.gui.api import (
    Api,
    KEEP_JSON_WHEN_AUTO_APPLY,
    MONITOR_POLL_FAST,
    MONITOR_POLL_SLOW,
)


def _bare_api(settings):
    api = Api.__new__(Api)
    api.settings = settings
    api.processed_pids = set()
    api.flag_manager = object()
    api.roblox_manager = object()
    return api


def test_keep_json_knob_defaults_on():
    assert KEEP_JSON_WHEN_AUTO_APPLY is True
    assert MONITOR_POLL_FAST < MONITOR_POLL_SLOW
    assert LAUNCH_INIT_POLL_SEC < 1.0


def test_should_wipe_false_when_auto_apply_on(monkeypatch):
    monkeypatch.setattr(RobloxManager, "startup_write_in_progress",
                        staticmethod(lambda: False))
    api = _bare_api({"auto_apply": True, "auto_clear_json": True})
    assert api._should_wipe_clientapp() is False


def test_should_wipe_true_when_auto_apply_off(monkeypatch):
    monkeypatch.setattr(RobloxManager, "startup_write_in_progress",
                        staticmethod(lambda: False))
    api = _bare_api({"auto_apply": False, "auto_clear_json": True})
    assert api._should_wipe_clientapp() is True


def test_should_wipe_false_when_user_disabled_clear(monkeypatch):
    monkeypatch.setattr(RobloxManager, "startup_write_in_progress",
                        staticmethod(lambda: False))
    api = _bare_api({"auto_apply": False, "auto_clear_json": False})
    assert api._should_wipe_clientapp() is False


def test_idle_clear_skips_when_auto_apply_on(monkeypatch):
    monkeypatch.setattr(RobloxManager, "startup_write_in_progress",
                        staticmethod(lambda: False))
    monkeypatch.setattr(RobloxManager, "clientapp_json_has_flags",
                        staticmethod(lambda: True))
    cleared = []
    api = _bare_api({"auto_apply": True, "auto_clear_json": True})

    class _RM:
        def find_roblox_process(self):
            return None

    api.roblox_manager = _RM()
    api.clear_clientapp_json = lambda: cleared.append(True)
    api._reconcile_idle_clear()
    assert cleared == []


def test_monitor_poll_fast_when_no_pid():
    api = _bare_api({"auto_apply": True})
    assert api._monitor_poll_seconds(None, True) == MONITOR_POLL_FAST


def test_monitor_poll_fast_while_new_pid_unprocessed():
    api = _bare_api({"auto_apply": True})
    assert api._monitor_poll_seconds(4242, True) == MONITOR_POLL_FAST


def test_monitor_poll_slow_once_pid_processed():
    api = _bare_api({"auto_apply": True})
    api.processed_pids.add(4242)
    assert api._monitor_poll_seconds(4242, True) == MONITOR_POLL_SLOW


def test_auto_apply_skip_json_when_file_has_flags(monkeypatch):
    monkeypatch.setattr(RobloxManager, "startup_write_in_progress",
                        staticmethod(lambda: False))
    monkeypatch.setattr(RobloxManager, "clientapp_json_has_flags",
                        staticmethod(lambda: True))
    api = _bare_api({"auto_apply": True})
    assert api._auto_apply_skip_json() is True


def test_auto_apply_skip_json_false_when_file_empty(monkeypatch):
    monkeypatch.setattr(RobloxManager, "startup_write_in_progress",
                        staticmethod(lambda: False))
    monkeypatch.setattr(RobloxManager, "clientapp_json_has_flags",
                        staticmethod(lambda: False))
    api = _bare_api({"auto_apply": True})
    assert api._auto_apply_skip_json() is False


def test_launch_and_apply_has_no_extra_one_second_wait():
    import inspect
    src = inspect.getsource(FlagManager.launch_and_apply)
    assert "LAUNCH_INIT_POLL_SEC" in src
    assert "time.sleep(1.0)" not in src
    assert "allocate flag objects" not in src
