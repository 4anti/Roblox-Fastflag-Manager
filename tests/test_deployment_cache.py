"""v4.0.4 Phase 2: the version-resolver caches CDN results and rate-limits
the failure log so the frontend's periodic status poll doesn't flood
Roblox's CDN or fill the console with "x60/min" warning collapses.

Contract locked in here:
- A positive result is cached for `_CACHE_TTL_OK` (5 min).
- A negative result is cached for `_CACHE_TTL_FAIL` (30 s) — no hammer.
- The warning log fires at most once per `_LOG_COOLDOWN` (60 s).
- Cross-thread callers serialize through `_CACHE_LOCK`.
"""
import threading
import unittest
from unittest import mock

from src.core.version_changer import deployment


class TestResolverCache(unittest.TestCase):
    def setUp(self):
        deployment._cache_reset()
        # Reset the log-cooldown state alongside the cache.
        deployment._CACHE['last_log_ts'] = 0.0

    def tearDown(self):
        deployment._cache_reset()

    def test_positive_cache_hit_skips_network(self):
        """A cached success is returned without touching the CDN."""
        with mock.patch.object(deployment, '_http_get_json',
                               return_value={'clientVersionUpload':
                                             'version-abc123'}) as m_json, \
             mock.patch.object(deployment, '_http_get_text') as m_text:
            first = deployment.get_latest_production_guid()
            self.assertEqual(first, 'version-abc123')
            self.assertEqual(m_json.call_count, 1)
            second = deployment.get_latest_production_guid()
            self.assertEqual(second, 'version-abc123')
            # No additional HTTP calls — cache hit.
            self.assertEqual(m_json.call_count, 1)
            self.assertEqual(m_text.call_count, 0)

    def test_negative_cache_hit_skips_network(self):
        """After a failure the next call within TTL must NOT hit the CDN."""
        with mock.patch.object(deployment, '_http_get_json',
                               return_value=None) as m_json, \
             mock.patch.object(deployment, '_http_get_text',
                               return_value=None) as m_text:
            first = deployment.get_latest_production_guid()
            self.assertIsNone(first)
            self.assertEqual(m_json.call_count, 1)
            self.assertEqual(m_text.call_count, 1)
            # 20 rapid retries within the 30 s failure TTL — none re-fetch.
            for _ in range(20):
                self.assertIsNone(deployment.get_latest_production_guid())
            self.assertEqual(m_json.call_count, 1)
            self.assertEqual(m_text.call_count, 1)

    def test_positive_ttl_expiry_re_fetches(self):
        """Past `_CACHE_TTL_OK` a fresh CDN call is issued."""
        t = [1000.0]
        with mock.patch.object(deployment.time, 'time',
                               side_effect=lambda: t[0]), \
             mock.patch.object(deployment, '_http_get_json',
                               return_value={'clientVersionUpload':
                                             'version-abc123'}) as m_json:
            deployment.get_latest_production_guid()
            t[0] += deployment._CACHE_TTL_OK + 1.0
            deployment.get_latest_production_guid()
            self.assertEqual(m_json.call_count, 2)

    def test_negative_ttl_expiry_re_fetches(self):
        """Past `_CACHE_TTL_FAIL` we try the CDN again."""
        t = [2000.0]
        with mock.patch.object(deployment.time, 'time',
                               side_effect=lambda: t[0]), \
             mock.patch.object(deployment, '_http_get_json',
                               return_value=None) as m_json, \
             mock.patch.object(deployment, '_http_get_text',
                               return_value=None):
            deployment.get_latest_production_guid()
            t[0] += deployment._CACHE_TTL_FAIL + 1.0
            deployment.get_latest_production_guid()
            self.assertEqual(m_json.call_count, 2)

    def test_log_rate_limited_during_outage(self):
        """The failure warning fires at most once per `_LOG_COOLDOWN`,
        even across many outage-induced cache misses."""
        t = [3000.0]
        log_calls = []
        with mock.patch.object(deployment.time, 'time',
                               side_effect=lambda: t[0]), \
             mock.patch.object(deployment, '_http_get_json',
                               return_value=None), \
             mock.patch.object(deployment, '_http_get_text',
                               return_value=None), \
             mock.patch.object(deployment, 'log',
                               side_effect=lambda *a, **k: log_calls.append(a)):
            # Fire 10 cache-miss cycles, each spaced past the failure TTL
            # so they always take the network path.
            for _ in range(10):
                deployment.get_latest_production_guid()
                t[0] += deployment._CACHE_TTL_FAIL + 1.0
            # Only ceil(10 * 31 / 60) ≈ 6 log calls would appear if the
            # cooldown weren't there. With the cooldown at 60 s and each
            # cycle spending 31 s of wall time, at most one log per 2
            # cycles — ≤ 6 log calls, and the exact count is bounded.
            # Loose upper bound is enough — the point is that it's not 10.
            self.assertLessEqual(len(log_calls), 6)
            # And it must have logged at least once (the first cycle).
            self.assertGreaterEqual(len(log_calls), 1)

    def test_concurrent_callers_do_not_deadlock(self):
        """Ten threads calling in parallel: none deadlock, all return the
        same cached value. Serves as a smoke test for the lock scope."""
        deployment._cache_reset()
        results = []

        def worker():
            results.append(deployment.get_latest_production_guid())

        with mock.patch.object(deployment, '_http_get_json',
                               return_value={'clientVersionUpload':
                                             'version-c0ffee01'}):
            threads = [threading.Thread(target=worker) for _ in range(10)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=5.0)
                self.assertFalse(th.is_alive(), 'resolver deadlocked')
        self.assertEqual(len(results), 10)
        self.assertTrue(all(r == 'version-c0ffee01' for r in results))

    def test_malformed_guid_treated_as_failure(self):
        """CDN returning garbage falls through to the fallback endpoint,
        then caches None on total failure."""
        with mock.patch.object(deployment, '_http_get_json',
                               return_value={'clientVersionUpload':
                                             'not a guid'}), \
             mock.patch.object(deployment, '_http_get_text',
                               return_value='also garbage'):
            self.assertIsNone(deployment.get_latest_production_guid())


if __name__ == '__main__':
    unittest.main()
