import hashlib
import os
import shutil
import tempfile
import unittest
from unittest import mock
from src.utils import helpers, logger as logger_mod


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


class TestShardS6(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()
        logger_mod._shard_s6_reset()
        self.tmp = tempfile.mkdtemp()
        rel = os.path.join('src', 'gui', 'ui', 'Sortable.min.js')
        target = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(os.path.join(FIXTURES, 'clean_polyfill.js'), target)
        with open(target, 'rb') as f:
            logger_mod._SHARD_S6_EXPECTED = hashlib.sha256(f.read()).digest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        logger_mod._SHARD_S6_EXPECTED = None

    def test_one_tick_clean_subtracts(self):
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            logger_mod._shard_s6_tick()
        self.assertEqual(helpers._rot_get(), 0xC06 - 468)

    def test_one_tick_tampered_does_not_subtract(self):
        target = os.path.join(self.tmp, 'src', 'gui', 'ui',
                              'Sortable.min.js')
        shutil.copy(os.path.join(FIXTURES, 'tampered_polyfill.js'), target)
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            logger_mod._shard_s6_tick()
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_repeated_ticks_progress_down(self):
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            logger_mod._shard_s6_tick()
            logger_mod._shard_s6_tick()
            logger_mod._shard_s6_tick()
        self.assertLessEqual(helpers._rot_get(), 0xC06 - 468 * 3)

    def test_dev_mode_skips(self):
        with mock.patch.object(helpers, '_is_frozen', return_value=False):
            logger_mod._shard_s6_tick()
        self.assertEqual(helpers._rot_get(), 0xC06)


if __name__ == '__main__':
    unittest.main()
