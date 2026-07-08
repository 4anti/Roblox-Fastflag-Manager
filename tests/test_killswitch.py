"""Tests for the kill switch + revert/remove flag operations.

These cover the FlagManager-level live-revert/re-enable logic and the API-level
remove-unavailable filtering (which must keep working json_only flags).
"""
import unittest

from src.core.flag_manager import FlagManager


class FakeRM:
    """Minimal stand-in for RobloxManager used by the live-write helpers."""

    def __init__(self, attached=True):
        self.is_attached = attached
        self.writes = []
        self.mem = {}

    def get_live_flag_address(self, name):
        return [{"abs_addr": 0x1000, "type": "int"}]

    def open_process_for_write(self):
        return True

    def write_flag_at_address(self, ftype, addr, value):
        self.writes.append((ftype, addr, value))
        self.mem[addr] = str(value)
        return True, "OK"

    def read_flag_at_address(self, ftype, addr):
        return self.mem.get(addr, "0")


class KillswitchBackendTests(unittest.TestCase):
    def _fm(self, attached=True):
        fm = FlagManager()
        # Don't touch disk during tests.
        fm.save_user_flags = lambda *a, **k: True
        fm._rm = FakeRM(attached=attached)
        return fm

    def test_disable_all_reverts_active_and_disables(self):
        fm = self._fm()
        fm.user_flags = [
            {"name": "A", "value": "9999", "type": "int", "enabled": True,
             "_was_active": True, "original_value": "60"},
            {"name": "B", "value": "true", "type": "bool", "enabled": True},  # never applied live
        ]
        summary = fm.disable_all_live()

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["reverted"], 1)  # only A had a live original
        self.assertTrue(all(f["enabled"] is False for f in fm.user_flags))
        self.assertIn(("int", 0x1000, "60"), fm._rm.writes)
        self.assertFalse(fm.user_flags[0]["_was_active"])

    def test_disable_all_no_revert_when_detached(self):
        fm = self._fm(attached=False)
        fm.user_flags = [
            {"name": "A", "value": "9999", "type": "int", "enabled": True,
             "_was_active": True, "original_value": "60"},
        ]
        summary = fm.disable_all_live()
        self.assertEqual(summary["reverted"], 0)
        self.assertFalse(fm.user_flags[0]["enabled"])
        self.assertEqual(fm._rm.writes, [])

    def test_re_enable_flags_intersects_existing(self):
        fm = self._fm()
        fm.user_flags = [
            {"name": "A", "enabled": False, "value": "1", "type": "int"},
            {"name": "B", "enabled": False, "value": "1", "type": "int"},
        ]
        count = fm.re_enable_flags(["A", "GHOST"])  # GHOST no longer exists
        self.assertEqual(count, 1)
        self.assertTrue(fm.user_flags[0]["enabled"])
        self.assertFalse(fm.user_flags[1]["enabled"])

    def test_revert_one_to_original_verifies(self):
        fm = self._fm()
        fm.user_flags = [
            {"name": "A", "value": "9999", "type": "int", "enabled": True,
             "_was_active": True, "original_value": "60"},
        ]
        res = fm.revert_one_to_original("A")
        self.assertTrue(res["ok"])
        self.assertTrue(res["verified"])  # read-back matched what we wrote
        self.assertIn(("int", 0x1000, "60"), fm._rm.writes)

    def test_revert_one_unknown_flag(self):
        fm = self._fm()
        fm.user_flags = []
        res = fm.revert_one_to_original("nope")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_found")

    def test_revert_string_flag_not_memory_writable(self):
        fm = self._fm()
        fm.user_flags = [{"name": "S", "value": "x", "type": "string", "enabled": True}]
        res = fm.revert_one_to_original("S")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_memory_writable")

    def test_revert_when_detached(self):
        fm = self._fm(attached=False)
        fm.user_flags = [
            {"name": "A", "value": "9999", "type": "int", "enabled": True,
             "_was_active": True, "original_value": "60"},
        ]
        res = fm.revert_one_to_original("A")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_attached")

    def test_watchdog_pause_resume(self):
        fm = self._fm()
        fm.pause_watchdog()
        self.assertTrue(fm._watchdog_paused)
        fm.resume_watchdog()
        self.assertFalse(fm._watchdog_paused)

    def test_set_killswitch_bind(self):
        fm = self._fm()
        fm.set_killswitch_bind("F8")
        self.assertEqual(fm._killswitch_bind, "F8")
        fm.set_killswitch_bind("")
        self.assertEqual(fm._killswitch_bind, "")


class RemoveUnavailableTests(unittest.TestCase):
    def test_remove_unavailable_clears_whole_group_and_saves_history(self):
        # api imports pywebview; skip cleanly if the GUI dep isn't installed.
        try:
            from src.gui.api import Api
        except Exception as e:  # pragma: no cover - environment dependent
            self.skipTest(f"api import unavailable: {e}")

        history_calls = []
        fm = FlagManager()
        fm.save_user_flags = lambda *a, **k: True
        fm.save_history_snapshot = lambda *a, **k: history_calls.append(a)
        fm.user_flags = [
            {"name": "ok", "_status": "success"},
            {"name": "jo", "_status": "json_only"},
            {"name": "bad", "_status": "failed"},
            {"name": "na", "_status": "unavailable"},
            {"name": "fresh", "_status": None},
        ]
        api = Api.__new__(Api)
        api.flag_manager = fm
        api.settings = {"history_limit": 30}

        res = api.remove_unavailable_flags()
        # Removes the whole "Unavailable" group (json_only + failed + unavailable);
        # keeps applied + not-yet-applied flags.
        self.assertEqual(res["removed"], 3)
        self.assertEqual({f["name"] for f in fm.user_flags}, {"ok", "fresh"})
        # Removing unavailable flags MUST be recorded in history.
        self.assertEqual(len(history_calls), 1)


if __name__ == "__main__":
    unittest.main()
