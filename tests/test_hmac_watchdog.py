"""Locks in v4.0.4's Phase 1 fix: the frontend heartbeat watchdog is
neutralized (`absence_tolerance_sec = +inf`) so `_hmac_watchdog_tripped()`
returns False regardless of how long the heartbeat reports False.

Prior to v4.0.4, a rolling ~60 s window of False ticks past the 15 s
startup grace flipped the tripped flag; `apply_flags_hybrid` then refused
with the "settings HMAC signature check failed" log line even though the
on-disk settings signature was fine. Legitimate users on narrow windows,
transient network variance, or Adsterra no-fill got nuked.

If a future refactor changes the tolerance back to a finite value, these
tests fail loudly.
"""
import math
import unittest
from unittest import mock

from src.utils import config


class TestWatchdogNeutralized(unittest.TestCase):
    def setUp(self):
        config._hmac_watchdog_reset()

    def tearDown(self):
        config._hmac_watchdog_reset()

    def test_tolerance_is_infinite(self):
        """The one-line contract of the fix. If someone reverts this,
        the whole neutralization is undone silently."""
        self.assertTrue(math.isinf(
            config._HMAC_WATCHDOG['absence_tolerance_sec']))

    def test_ten_minutes_of_false_ticks_never_trip(self):
        """Simulate 10 minutes of the frontend reporting `ok=False`
        every 5 s past the startup grace. Watchdog must stay clean."""
        # Time base: t0 = 1000. First tick seeds the watchdog.
        t = [1000.0]
        with mock.patch.object(config._hmac_time, 'time',
                               side_effect=lambda: t[0]):
            config._hmac_health_tick(False)   # seed
            # Advance past the 15 s startup grace, then feed False every 5 s
            # for 10 minutes (120 ticks).
            for _ in range(120):
                t[0] += 5.0
                config._hmac_health_tick(False)
            self.assertFalse(config._hmac_watchdog_tripped())

    def test_true_ticks_still_recorded(self):
        """A True tick must still update `last_ok_ts` even though the
        trip condition can never fire. Keeps the telemetry live for the
        Phase 2 replacement to layer on."""
        t = [2000.0]
        with mock.patch.object(config._hmac_time, 'time',
                               side_effect=lambda: t[0]):
            config._hmac_health_tick(False)   # seed at t=2000, both ts set
            t[0] += 30.0                       # past grace
            config._hmac_health_tick(True)
            self.assertEqual(config._HMAC_WATCHDOG['last_ok_ts'], 2030.0)
            self.assertFalse(config._hmac_watchdog_tripped())

    def test_mixed_true_false_sequence_never_trips(self):
        """Even hostile mix — one True then 500 False — must not trip."""
        t = [3000.0]
        with mock.patch.object(config._hmac_time, 'time',
                               side_effect=lambda: t[0]):
            config._hmac_health_tick(True)     # seed
            t[0] += 100.0
            config._hmac_health_tick(True)
            for _ in range(500):
                t[0] += 5.0
                config._hmac_health_tick(False)
            self.assertFalse(config._hmac_watchdog_tripped())


if __name__ == '__main__':
    unittest.main()
