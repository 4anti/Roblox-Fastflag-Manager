"""v4.0.4: log messages containing non-ASCII characters must not raise
UnicodeEncodeError from the underlying print() call. On Windows the
default console codec is cp1252 or cp437, neither of which encodes
arrows (U+2192), box-drawing, non-Latin scripts, or emoji.

The prior implementation called `print(formatted_msg)` directly. Any
caller that logged a message with non-ASCII would (a) crash their own
call path, (b) propagate the exception out to unrelated error handlers
that would misclassify the failure — notably the ad-network probe was
observed to catch a print() UnicodeEncodeError raised during its own
success log and report `[net-probe] UNREACHABLE — unclassified network
failure`, contradicting the already-logged REACHABLE line.

Fix: `_safe_print` catches UnicodeEncodeError / OSError and falls back
to `encode('ascii', 'replace')`. sys.stdout is also reconfigured to
UTF-8 at import time on Python 3.7+ so replacement rarely fires.
"""
import threading
import unittest
from collections import deque
from unittest import mock

from src.utils import logger


def _fresh():
    lg = logger.Logger.__new__(logger.Logger)
    lg.console_log = deque(maxlen=1000)
    lg._total = 0
    lg._last_core = None
    lg._repeat_count = 1
    lg._tail_epoch = 0
    lg.lock = threading.Lock()
    return lg


class TestSafePrint(unittest.TestCase):
    def test_safe_print_swallows_unicode_encode_error(self):
        """Simulate a Windows cp1252 stdout: the first print() raises
        UnicodeEncodeError; the fallback must succeed silently."""
        arrow_msg = "hello \u2192 world"
        # First print() raises, second (ascii-replaced) succeeds.
        with mock.patch('builtins.print',
                        side_effect=[UnicodeEncodeError('charmap',
                                                       arrow_msg, 6, 7,
                                                       'no cp1252'),
                                     None]) as m:
            logger._safe_print(arrow_msg)
            self.assertEqual(m.call_count, 2)
            # Fallback message stripped of the arrow.
            self.assertNotIn('\u2192', m.call_args_list[1].args[0])

    def test_safe_print_swallows_os_error(self):
        """stdout closed / pipe broken must not propagate."""
        with mock.patch('builtins.print',
                        side_effect=OSError('bad pipe')):
            # No assertion needed: raising propagates and fails the test.
            logger._safe_print("plain ascii")

    def test_safe_print_both_stages_can_fail(self):
        """If both the raw and ascii-fallback print() raise, we still
        must not propagate — the logger is best-effort."""
        with mock.patch('builtins.print',
                        side_effect=[UnicodeEncodeError('c', 'x', 0, 1,
                                                       'r'),
                                     OSError('bad')]):
            logger._safe_print("hello \u2192 world")


class TestLogCallDoesNotRaiseOnUnicode(unittest.TestCase):
    def test_log_with_arrow_does_not_raise(self):
        """The exact scenario from the reported incident: a log call
        containing U+2192 must not propagate an exception."""
        lg = _fresh()
        # Force print() to raise on the U+2192 payload the way a Windows
        # cp1252 stdout would.
        def fake_print(msg):
            if '\u2192' in msg:
                raise UnicodeEncodeError('charmap', msg, 0, 1, 'boom')
        with mock.patch('builtins.print', side_effect=fake_print):
            # If _safe_print did not catch, this raises and the test fails.
            lg.log("REACHABLE 200 51473B \u2192 ad network is fine")
        # Message still landed in the deque.
        self.assertEqual(lg._total, 1)


if __name__ == '__main__':
    unittest.main()
