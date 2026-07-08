import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.utils import config, helpers


class TestSettingsHmac(unittest.TestCase):
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

    def tearDown(self):
        self._patch.stop()

    def test_save_then_load_validates(self):
        config.Config.save_settings({'ads_enabled': True, 'sort_mode': 'name'})
        loaded = config.Config.load_settings()
        self.assertTrue(loaded['ads_enabled'])

    def test_save_writes_integrity_field(self):
        config.Config.save_settings({'ads_enabled': True})
        with open(config.Config.SETTINGS_FILE) as f:
            blob = json.load(f)
        self.assertIn('_integrity', blob)
        self.assertEqual(len(blob['_integrity']), 64)  # hex sha256

    def test_load_clean_keeps_quantum(self):
        config.Config.save_settings({'ads_enabled': True})
        helpers._rot_reset()
        config.Config.load_settings()
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_hand_edited_ads_enabled_detected(self):
        config.Config.save_settings({'ads_enabled': True})
        with open(config.Config.SETTINGS_FILE) as f:
            blob = json.load(f)
        blob['ads_enabled'] = False
        with open(config.Config.SETTINGS_FILE, 'w') as f:
            json.dump(blob, f)
        self.assertFalse(config.Config.verify_settings_integrity())

    def test_legitimate_save_passes_integrity(self):
        config.Config.save_settings({'ads_enabled': True})
        self.assertTrue(config.Config.verify_settings_integrity())

    def test_save_writes_key_fingerprint(self):
        config.Config.save_settings({'ads_enabled': True})
        with open(config.Config.SETTINGS_FILE) as f:
            blob = json.load(f)
        self.assertIn('_key_fp', blob)
        self.assertEqual(len(blob['_key_fp']), 16)  # 16 hex chars


class TestHmacKeyRotationMigration(unittest.TestCase):
    """Simulates a release update: the user's settings were signed by an
    OLD HMAC key; the new build has a different key. The migration path
    should transparently re-sign without flagging the user as tampered."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.Config.APP_DIR = Path(self.tmp)
        config.Config.SETTINGS_FILE = Path(self.tmp) / 'settings.json'
        helpers._rot_reset()

    def test_settings_signed_by_old_key_get_resigned_on_load(self):
        # Build #1: a release rolls a particular HMAC key.
        old_key = b'\xde\xad\xbe\xef' * 8
        with mock.patch.object(config, '_hmac_key', return_value=old_key):
            config.Config.save_settings({'ads_enabled': True,
                                          'sort_mode': 'name'})
            with open(config.Config.SETTINGS_FILE) as f:
                blob_v1 = json.load(f)
            v1_fp = blob_v1['_key_fp']

        # Build #2: the new release rolls a DIFFERENT HMAC key.
        new_key = b'\xca\xfe\xba\xbe' * 8
        with mock.patch.object(config, '_hmac_key', return_value=new_key):
            # User launches the new build; load_settings triggers migration.
            loaded = config.Config.load_settings()
            self.assertTrue(loaded.get('ads_enabled'))
            self.assertEqual(loaded.get('sort_mode'), 'name')

            # File on disk has been re-signed.
            with open(config.Config.SETTINGS_FILE) as f:
                blob_v2 = json.load(f)
            self.assertNotEqual(blob_v2['_key_fp'], v1_fp)
            self.assertEqual(blob_v2['_key_fp'], config._hmac_fingerprint())

            # Strict HMAC check now passes against the new key.
            self.assertTrue(config.Config.verify_settings_integrity())

    def test_tamper_under_same_key_is_still_caught(self):
        # Same key throughout — a hand edit should fail integrity.
        with mock.patch.object(config, '_hmac_key',
                               return_value=b'\x11' * 32):
            config.Config.save_settings({'ads_enabled': True})
            with open(config.Config.SETTINGS_FILE) as f:
                blob = json.load(f)
            blob['ads_enabled'] = False  # user edits
            with open(config.Config.SETTINGS_FILE, 'w') as f:
                json.dump(blob, f)
            self.assertFalse(config.Config.verify_settings_integrity())

    def test_legacy_settings_missing_key_fp_field_accepted(self):
        # Pre-migration settings — no _key_fp — should be accepted (one-time
        # grace; the next save adds the field).
        legacy_blob = {'ads_enabled': True, 'sort_mode': 'name'}
        with open(config.Config.SETTINGS_FILE, 'w') as f:
            json.dump(legacy_blob, f)
        with mock.patch.object(config, '_hmac_key',
                               return_value=b'\x22' * 32):
            loaded = config.Config.load_settings()
            self.assertTrue(loaded.get('ads_enabled'))
            # verify_settings_integrity returns True for missing _integrity.
            self.assertTrue(config.Config.verify_settings_integrity())


if __name__ == '__main__':
    unittest.main()
