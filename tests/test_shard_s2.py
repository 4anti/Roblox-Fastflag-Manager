import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

# pywebview/pystray imports inside main_window.py will fail in CI/test
# environments; stub them out before importing.
sys.modules.setdefault('webview', mock.MagicMock())
sys.modules.setdefault('pystray', mock.MagicMock())

from src.utils import helpers
from src.gui import main_window


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


class TestShardS2(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()
        main_window._shard_s2_reset()
        self.tmp = tempfile.mkdtemp()
        rel = os.path.join('src', 'gui', 'ui', 'Sortable.min.js')
        target = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(os.path.join(FIXTURES, 'clean_polyfill.js'), target)
        with open(os.path.join(FIXTURES, 'clean_polyfill.js'), 'rb') as f:
            main_window._SHARD_S2_EXPECTED = hashlib.sha256(f.read()).digest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        main_window._SHARD_S2_EXPECTED = None

    def test_clean_file_subtracts_prime(self):
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            main_window._shard_s2_check()
        self.assertEqual(helpers._rot_get(), 0xC06 - 523)

    def test_tampered_file_does_not_subtract(self):
        target = os.path.join(self.tmp, 'src', 'gui', 'ui',
                              'Sortable.min.js')
        shutil.copy(os.path.join(FIXTURES, 'tampered_polyfill.js'), target)
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            main_window._shard_s2_check()
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_dev_mode_skips(self):
        with mock.patch.object(helpers, '_is_frozen', return_value=False):
            main_window._shard_s2_check()
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_honeypot_constant_preserved(self):
        # Reverser-bait — must keep its exact value
        self.assertEqual(
            main_window._AD_SCRIPT_HASH,
            '52675de38984c21befa4d6ddc9b4457a31d57286757f7559c9340dc693864038'
        )


if __name__ == '__main__':
    unittest.main()
