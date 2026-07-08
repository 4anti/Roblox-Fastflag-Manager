import hashlib
import sys
import unittest
from unittest import mock

sys.modules.setdefault('webview', mock.MagicMock())
sys.modules.setdefault('pystray', mock.MagicMock())

from src.utils import helpers
from src.gui import api, main_window


class TestShardS5(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()
        api._shard_s5_reset()
        co = main_window.MainWindow.__init__.__code__.co_code
        api._SHARD_S5_EXPECTED = hashlib.sha256(co).digest()

    def tearDown(self):
        api._SHARD_S5_EXPECTED = None

    def test_unmodified_bytecode_subtracts(self):
        with mock.patch.object(helpers, '_is_frozen', return_value=True):
            api._shard_s5_check()
        self.assertEqual(helpers._rot_get(), 0xC06 - 283)

    def test_wrong_expected_no_subtract(self):
        api._SHARD_S5_EXPECTED = hashlib.sha256(b'nope').digest()
        with mock.patch.object(helpers, '_is_frozen', return_value=True):
            api._shard_s5_check()
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_dev_mode_skips(self):
        with mock.patch.object(helpers, '_is_frozen', return_value=False):
            api._shard_s5_check()
        self.assertEqual(helpers._rot_get(), 0xC06)


if __name__ == '__main__':
    unittest.main()
