import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Stub heavy native deps before api import.
sys.modules.setdefault('webview', mock.MagicMock())
sys.modules.setdefault('pystray', mock.MagicMock())

from src.utils import config, helpers


class TestShardS3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.Config.APP_DIR = Path(self.tmp)
        config.Config.SETTINGS_FILE = Path(self.tmp) / 'settings.json'
        self._patch = mock.patch.object(
            config, '_hmac_key',
            return_value=b'\xde\xad\xbe\xef' * 8
        )
        self._patch.start()
        helpers._rot_reset()
        from src.gui import api
        self.api = api

    def tearDown(self):
        self._patch.stop()

    def _new_api_with_settings(self, settings_blob):
        a = self.api.Api.__new__(self.api.Api)
        a.settings = settings_blob
        return a

    def test_clean_settings_returns_true_and_subtracts(self):
        config.Config.save_settings({'ads_enabled': True})
        a = self._new_api_with_settings(config.Config.load_settings())
        with mock.patch.object(helpers, '_is_frozen', return_value=True):
            result = a.get_content_filter()
        self.assertTrue(result)
        self.assertEqual(helpers._rot_get(), 0xC06 - 419)

    def test_tampered_ads_disabled_returns_true_no_subtract(self):
        config.Config.save_settings({'ads_enabled': True})
        with open(config.Config.SETTINGS_FILE) as f:
            blob = json.load(f)
        blob['ads_enabled'] = False
        with open(config.Config.SETTINGS_FILE, 'w') as f:
            json.dump(blob, f)
        a = self._new_api_with_settings(json.load(open(config.Config.SETTINGS_FILE)))
        with mock.patch.object(helpers, '_is_frozen', return_value=True):
            result = a.get_content_filter()
        # Forced True despite the user's edit
        self.assertTrue(result)
        # No subtraction because HMAC failed
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_dev_mode_returns_settings_value_no_subtract(self):
        a = self._new_api_with_settings({'ads_enabled': False})
        with mock.patch.object(helpers, '_is_frozen', return_value=False):
            result = a.get_content_filter()
        self.assertFalse(result)
        self.assertEqual(helpers._rot_get(), 0xC06)


if __name__ == '__main__':
    unittest.main()
