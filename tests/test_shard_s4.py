import hashlib
import os
import shutil
import tempfile
import unittest
from unittest import mock
from src.utils import helpers
from src.core import preset_manager


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def _expected_region_hash(html_path):
    with open(html_path, 'rb') as f:
        data = f.read()
    idx = data.find(b'<script src="Sortable.min.js')
    region = data[max(0, idx-32):idx+224]
    return hashlib.sha256(region).digest()


class TestShardS4(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()
        preset_manager._shard_s4_reset()
        self.tmp = tempfile.mkdtemp()
        rel = os.path.join('src', 'gui', 'ui', 'index.html')
        target = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(os.path.join(FIXTURES, 'clean_index.html'), target)
        preset_manager._SHARD_S4_EXPECTED = _expected_region_hash(target)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        preset_manager._SHARD_S4_EXPECTED = None

    def test_clean_html_subtracts(self):
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            preset_manager._shard_s4_check()
        self.assertEqual(helpers._rot_get(), 0xC06 - 601)

    def test_missing_script_tag_does_not_subtract(self):
        target = os.path.join(self.tmp, 'src', 'gui', 'ui', 'index.html')
        with open(target, 'w') as f:
            f.write('<html><body>no script here</body></html>')
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            preset_manager._shard_s4_check()
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_dev_mode_skips(self):
        with mock.patch.object(helpers, '_is_frozen', return_value=False):
            preset_manager._shard_s4_check()
        self.assertEqual(helpers._rot_get(), 0xC06)


if __name__ == '__main__':
    unittest.main()
