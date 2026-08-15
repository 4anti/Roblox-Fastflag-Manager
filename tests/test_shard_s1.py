import hashlib
import os
import shutil
import tempfile
import unittest
from unittest import mock
from src.utils import helpers


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


class TestShardS1(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()
        helpers._shard_s1_reset()
        self.tmp = tempfile.mkdtemp()
        rel = os.path.join('src', 'gui', 'ui', 'Sortable.min.js')
        target = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(os.path.join(FIXTURES, 'clean_polyfill.js'), target)
        with open(os.path.join(FIXTURES, 'clean_polyfill.js'), 'rb') as f:
            self.expected = hashlib.sha256(f.read(1024)).digest()
        helpers._SHARD_S1_EXPECTED = self.expected

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        helpers._SHARD_S1_EXPECTED = None

    def test_clean_file_subtracts_prime(self):
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            helpers.get_resource_path('src/gui/ui/Sortable.min.js')
        self.assertEqual(helpers._rot_get(), 0xC06 - 347)

    def test_tampered_file_does_not_subtract(self):
        target = os.path.join(self.tmp, 'src', 'gui', 'ui',
                              'Sortable.min.js')
        shutil.copy(os.path.join(FIXTURES, 'tampered_polyfill.js'), target)
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            helpers.get_resource_path('src/gui/ui/Sortable.min.js')
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_dev_mode_skips_check(self):
        with mock.patch.object(helpers, '_is_frozen', return_value=False):
            helpers.get_resource_path('anything')
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_shard_fires_only_once(self):
        with mock.patch.object(helpers._sys, '_MEIPASS', self.tmp, create=True), \
             mock.patch.object(helpers, '_is_frozen', return_value=True):
            helpers.get_resource_path('src/gui/ui/Sortable.min.js')
            helpers.get_resource_path('src/gui/ui/Sortable.min.js')
        self.assertEqual(helpers._rot_get(), 0xC06 - 347)


if __name__ == '__main__':
    unittest.main()
