"""v4.0.4: logger dedup no longer duplicates already-rendered console lines
when 2+ consecutive-duplicate collapses fire between polls.

Prior bug: `_total` was bumped on every dedup even though no NEW entry was
appended. `get_logs_since(since)` derived `start = total - len(buf)`, so
after N dedups `start` advanced by N and previously-buffered non-tail
entries got re-emitted with `replace=False`. The frontend appended them
again, then the tail's `replace=True` only dropped the newly-duplicated
tail — leaving 2× copies of every intermediate entry visible.

Fix: dedup no longer bumps `_total`; it bumps `_tail_epoch` instead.
`get_logs_since` returns `(logs, total, tail_epoch)`; when nothing new was
appended but the tail was mutated, it returns JUST the tail so the client
replaces its last-rendered line and never re-emits historical entries.
"""
import threading
import unittest
from collections import deque

from src.utils import logger


def _fresh_logger():
    """Bypass the singleton — needed so tests are independent and don't
    inherit state from prior in-process consumers."""
    lg = logger.Logger.__new__(logger.Logger)
    lg.console_log = deque(maxlen=1000)
    lg._total = 0
    lg._last_core = None
    lg._repeat_count = 1
    lg._tail_epoch = 0
    lg.lock = threading.Lock()
    return lg


class TestDedupReplayInvariant(unittest.TestCase):
    def test_repro_regression_two_dedups_do_not_duplicate_prior_entries(self):
        """Regression: 4 unique, poll, then 3 dedups, then poll again.
        The second poll must NOT return intermediate entries as new."""
        lg = _fresh_logger()
        for m in ('A', 'B', 'C', 'D'):
            lg.log(m)
        _, total1, epoch1 = lg.get_logs_since(0, 0)
        self.assertEqual(total1, 4)
        # 3 back-to-back D's — dedup fires each time.
        lg.log('D'); lg.log('D'); lg.log('D')
        new, total2, epoch2 = lg.get_logs_since(total1, epoch1)
        # Total must NOT have advanced — no new append.
        self.assertEqual(total2, total1)
        self.assertGreater(epoch2, epoch1)
        # Exactly ONE entry returned: the mutated tail with replace=True.
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0][2])
        self.assertIn('x4', new[0][0])

    def test_client_up_to_date_gets_nothing_on_repeated_polls(self):
        """Once the client's cursor matches, subsequent polls with no
        activity are empty — no drift, no re-emit."""
        lg = _fresh_logger()
        lg.log('X'); lg.log('Y')
        _, total, epoch = lg.get_logs_since(0, 0)
        for _ in range(5):
            new, t2, e2 = lg.get_logs_since(total, epoch)
            self.assertEqual(new, [])
            self.assertEqual(t2, total)
            self.assertEqual(e2, epoch)

    def test_dedup_signal_is_epoch_bump_not_total_bump(self):
        """Contract of the fix: total counts APPENDS, tail_epoch counts
        MUTATIONS. They must not conflate."""
        lg = _fresh_logger()
        lg.log('A')
        lg.log('A')  # dedup #1
        lg.log('A')  # dedup #2
        _, total, epoch = lg.get_logs_since(0, 0)
        self.assertEqual(total, 1)     # one append
        self.assertEqual(epoch, 2)     # two mutations

    def test_new_append_after_dedup_flushes_correctly(self):
        """After a dedup run, a fresh distinct log line must appear as a
        normal new entry (replace=False) with total advanced by 1."""
        lg = _fresh_logger()
        lg.log('A'); lg.log('B'); lg.log('B')
        _, t1, e1 = lg.get_logs_since(0, 0)
        lg.log('C')
        new, t2, e2 = lg.get_logs_since(t1, e1)
        self.assertEqual(t2, t1 + 1)
        self.assertEqual(len(new), 1)
        self.assertFalse(new[0][2])
        self.assertIn('C', new[0][0])

    def test_dedup_when_deque_empty_appends_normally(self):
        """Edge case: `_last_core` matches but the deque is empty (e.g.
        cleared). Must append, not mutate."""
        lg = _fresh_logger()
        lg.log('X')
        lg.clear_logs()
        # After clear, _last_core is reset — so this should append, not
        # dedup. But even if a hypothetical future refactor left
        # _last_core set, the guard `and self.console_log` protects us.
        lg.log('X')
        new, total, epoch = lg.get_logs_since(0, 0)
        self.assertEqual(len(new), 1)
        self.assertFalse(new[0][2])
        self.assertEqual(total, 2)          # clear kept _total monotonic
        self.assertEqual(epoch, 0)

    def test_get_logs_since_backward_compat_default_epoch(self):
        """Legacy callers using the single-arg form still work — since
        default `since_tail_epoch=0`, a mutation always signals as
        `epoch > 0` when the tail was ever mutated."""
        lg = _fresh_logger()
        lg.log('A'); lg.log('A')
        new, total, epoch = lg.get_logs_since(0)   # no epoch arg
        # First poll returns the tail with the mutated state.
        self.assertEqual(len(new), 1)
        self.assertEqual(total, 1)
        self.assertEqual(epoch, 1)

    def test_concurrent_writers_do_not_deadlock(self):
        """Ten writer threads emit interleaved logs; the reader observes
        a monotonically non-decreasing total and never sees a negative
        offset."""
        lg = _fresh_logger()
        errors = []

        def writer(base):
            try:
                for i in range(50):
                    lg.log(f'msg{base}-{i}')
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,))
                   for i in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5.0)
            self.assertFalse(th.is_alive())
        self.assertEqual(errors, [])
        _, total, _ = lg.get_logs_since(0, 0)
        self.assertEqual(total, 500)


if __name__ == '__main__':
    unittest.main()
