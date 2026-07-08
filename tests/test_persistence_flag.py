import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.utils import helpers


class TestPersistenceFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patch = mock.patch.object(
            helpers, '_persistence_flag_path',
            return_value=Path(self.tmp) / '.log_state'
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_no_file_means_clean(self):
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_writing_dirty_then_reading_dirty(self):
        helpers._persistence_flag_mark_dirty()
        self.assertTrue(helpers._persistence_flag_is_dirty())

    def test_clean_marker_with_current_sig_reads_clean(self):
        helpers._persistence_flag_mark_clean()
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_garbage_marker_reads_clean(self):
        # Disk corruption / AV scanner / stray bytes should not be misread
        # as tamper. The live shards are the authoritative detector.
        path = Path(self.tmp) / '.log_state'
        path.write_bytes(b'XX')
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_foreign_build_dirty_marker_ignored(self):
        # A dirty marker stamped with a DIFFERENT build's signature is
        # treated as no signal — the user has updated and we don't carry
        # over a prior release's dirty state.
        path = Path(self.tmp) / '.log_state'
        path.write_bytes(b'X1' + b'\x00' * 8)
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_legacy_unstamped_marker_ignored(self):
        # Old-format 'OK'/'X1' markers (pre-fix-B) are ignored as
        # foreign-signature.
        path = Path(self.tmp) / '.log_state'
        path.write_bytes(b'OK')
        self.assertFalse(helpers._persistence_flag_is_dirty())
        path.write_bytes(b'X1')
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_idempotent_mark_dirty(self):
        helpers._persistence_flag_mark_dirty()
        helpers._persistence_flag_mark_dirty()
        self.assertTrue(helpers._persistence_flag_is_dirty())


class TestQuantumPersistencePropagation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patch = mock.patch.object(
            helpers, '_persistence_flag_path',
            return_value=Path(self.tmp) / '.log_state'
        )
        self._patch.start()
        helpers._rot_reset()
        helpers._persistence_observer_reset()

    def tearDown(self):
        self._patch.stop()

    def test_clean_quantum_does_not_persist(self):
        for p in (347, 523, 419, 601, 283, 468, 437):
            helpers._rot_subtract(p)
        helpers._persistence_observer_check()
        self.assertFalse(helpers._persistence_flag_is_dirty())

    def test_dirty_quantum_persists_on_next_check(self):
        helpers._rot_subtract(347)  # only one shard ran
        helpers._persistence_observer_check()
        self.assertTrue(helpers._persistence_flag_is_dirty())

    def test_observer_only_writes_once(self):
        helpers._rot_subtract(347)
        helpers._persistence_observer_check()
        first = (Path(self.tmp) / '.log_state').read_bytes()
        helpers._persistence_observer_check()
        second = (Path(self.tmp) / '.log_state').read_bytes()
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
