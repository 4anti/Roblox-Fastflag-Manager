import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.modules.setdefault('webview', mock.MagicMock())
sys.modules.setdefault('pystray', mock.MagicMock())

from src.utils import helpers, logger as logger_mod, config
from src.gui import main_window, api
from src.core import preset_manager, flag_manager


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


class TestE2ETamperFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.flag_dir = Path(tempfile.mkdtemp())
        self.app_dir = Path(tempfile.mkdtemp())

        # Stage the bundled JS + HTML
        for rel, fixture in [
            (os.path.join('src', 'gui', 'ui', 'Sortable.min.js'),
             'clean_polyfill.js'),
            (os.path.join('src', 'gui', 'ui', 'index.html'),
             'clean_index.html'),
        ]:
            target = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy(os.path.join(FIXTURES, fixture), target)

        # Pin expected hashes against the staged clean copies
        with open(os.path.join(self.tmp, 'src', 'gui', 'ui',
                               'Sortable.min.js'), 'rb') as f:
            data = f.read()
        full_hash = hashlib.sha256(data).digest()
        prefix_hash = hashlib.sha256(data[:1024]).digest()
        helpers._SHARD_S1_EXPECTED = prefix_hash
        main_window._SHARD_S2_EXPECTED = full_hash
        logger_mod._SHARD_S6_EXPECTED = full_hash
        with open(os.path.join(self.tmp, 'src', 'gui', 'ui',
                               'index.html'), 'rb') as f:
            html = f.read()
        idx = html.find(b'<script src="Sortable.min.js')
        region = html[max(0, idx-32):idx+224]
        preset_manager._SHARD_S4_EXPECTED = hashlib.sha256(region).digest()
        api._SHARD_S5_EXPECTED = hashlib.sha256(
            main_window.MainWindow.__init__.__code__.co_code
        ).digest()

        # S7: stage a fake main.pyw at the mocked _MEIPASS root and pin the
        # expected hash to its bytes so the shard's own check matches.
        fake_main = b"# test fixture main.pyw\n"
        with open(os.path.join(self.tmp, 'main.pyw'), 'wb') as _f:
            _f.write(fake_main)
        flag_manager._SHARD_S7_EXPECTED = hashlib.sha256(fake_main).digest()

        # Settings store
        config.Config.APP_DIR = self.app_dir
        config.Config.SETTINGS_FILE = self.app_dir / 'settings.json'
        self.hmac_patch = mock.patch.object(
            config, '_hmac_key',
            return_value=b'\xde\xad\xbe\xef' * 8
        )
        self.hmac_patch.start()

        self.flag_patch = mock.patch.object(
            helpers, '_persistence_flag_path',
            return_value=self.flag_dir / '.log_state'
        )
        self.flag_patch.start()
        self.meipass_patch = mock.patch.object(
            helpers._sys, '_MEIPASS', self.tmp, create=True
        )
        self.meipass_patch.start()
        self.frozen_patch = mock.patch.object(
            helpers, '_is_frozen', return_value=True
        )
        self.frozen_patch.start()

    def tearDown(self):
        self.frozen_patch.stop()
        self.meipass_patch.stop()
        self.flag_patch.stop()
        self.hmac_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(str(self.flag_dir), ignore_errors=True)
        shutil.rmtree(str(self.app_dir), ignore_errors=True)
        helpers._SHARD_S1_EXPECTED = None
        main_window._SHARD_S2_EXPECTED = None
        logger_mod._SHARD_S6_EXPECTED = None
        preset_manager._SHARD_S4_EXPECTED = None
        api._SHARD_S5_EXPECTED = None
        flag_manager._SHARD_S7_EXPECTED = None

    def _reset_shards(self):
        helpers._shard_s1_reset()
        main_window._shard_s2_reset()
        preset_manager._shard_s4_reset()
        api._shard_s5_reset()
        logger_mod._shard_s6_reset()
        helpers._persistence_observer_reset()

    def _run_all_shards(self):
        """Drive every gate so a clean run lands quantum on 0."""
        helpers.get_resource_path('src/gui/ui/Sortable.min.js')  # S1
        main_window._shard_s2_check()                                     # S2
        # S3 — settings HMAC check via get_content_filter
        config.Config.save_settings({'ads_enabled': True})
        a = api.Api.__new__(api.Api)
        a.settings = config.Config.load_settings()
        a.get_content_filter()
        preset_manager._shard_s4_check()                                  # S4
        api._shard_s5_check()                                             # S5
        logger_mod._shard_s6_tick()                                       # S6
        flag_manager._shard_s7_reset()
        flag_manager._shard_s7_check()                                    # S7

    def test_clean_session_no_dirty_marker(self):
        helpers._rot_reset()
        self._reset_shards()
        self._run_all_shards()
        # All seven should subtract: 347+523+419+601+283+468+437 = 3078 = 0xC06
        self.assertEqual(helpers._rot_get(), 0)
        self.assertFalse(helpers._rot_is_dirty())
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_tamper_then_relaunch_stays_dirty(self):
        # Session 1: tamper the JS
        helpers._rot_reset()
        self._reset_shards()
        target = os.path.join(self.tmp, 'src', 'gui', 'ui',
                              'Sortable.min.js')
        shutil.copy(os.path.join(FIXTURES, 'tampered_polyfill.js'), target)
        helpers.get_resource_path('src/gui/ui/Sortable.min.js')
        main_window._shard_s2_check()
        logger_mod._shard_s6_tick()
        helpers._persistence_observer_check()
        self.assertTrue(helpers._persistence_flag_is_dirty())

        # Session 2: file restored
        shutil.copy(os.path.join(FIXTURES, 'clean_polyfill.js'), target)
        self._reset_shards()
        helpers._rot_bootstrap()  # reads dirty flag
        helpers.get_resource_path('src/gui/ui/Sortable.min.js')
        main_window._shard_s2_check()
        logger_mod._shard_s6_tick()
        # Bootstrap added +1000, three shards subtract 1338
        # 0xC06 + 1000 - 1338 = 0xC06 - 338, still dirty
        self.assertTrue(helpers._rot_is_dirty())


if __name__ == '__main__':
    unittest.main()
