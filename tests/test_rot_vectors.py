import sys
import unittest
from unittest import mock

sys.modules.setdefault('webview', mock.MagicMock())
sys.modules.setdefault('pystray', mock.MagicMock())

from src.utils import helpers
from src.gui import api
from src.core import preset_manager


class TestR1FlagSkip(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()

    def test_clean_state_passes_dict_unchanged(self):
        flags = {'F1': 'a', 'F2': 'b', 'F3': 'c'}
        with mock.patch('random.random', return_value=0.0):
            result = api._r1_filter(flags)
        self.assertEqual(result, flags)

    def test_dirty_state_high_quantum_drops_keys(self):
        helpers._quantum = 9000
        flags = {f'F{i}': str(i) for i in range(100)}
        with mock.patch('random.random', return_value=0.1):
            result = api._r1_filter(flags)
        self.assertLess(len(result), len(flags))

    def test_dirty_state_low_roll_keeps_keys(self):
        helpers._quantum = 5000
        flags = {f'F{i}': str(i) for i in range(100)}
        with mock.patch('random.random', return_value=0.99):
            result = api._r1_filter(flags)
        self.assertEqual(len(result), len(flags))

    def test_filter_never_drops_more_than_two(self):
        helpers._quantum = 9999
        flags = {f'F{i}': str(i) for i in range(100)}
        with mock.patch('random.random', return_value=0.0):
            result = api._r1_filter(flags)
        self.assertGreaterEqual(len(result), len(flags) - 2)


class TestR2PresetCorruption(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()

    def test_clean_state_passes_through(self):
        presets = [{'id': '1', 'flags': {'F': 'true'}}]
        with mock.patch('random.random', return_value=0.0):
            result = preset_manager._r2_smear(presets)
        self.assertEqual(result, presets)

    def test_dirty_state_high_roll_passes_through(self):
        helpers._quantum = 9000
        presets = [{'id': '1', 'flags': {'F': 'true'}}]
        with mock.patch('random.random', return_value=0.99):
            result = preset_manager._r2_smear(presets)
        self.assertEqual(result, presets)

    def test_dirty_state_low_roll_mutates_one_value(self):
        helpers._quantum = 9000
        presets = [{'id': '1', 'flags': {'F': 'true', 'G': 'false'}}]
        with mock.patch('random.random', return_value=0.01):
            with mock.patch('random.choice',
                            side_effect=lambda seq: seq[0]):
                result = preset_manager._r2_smear(presets)
        original_values = list(presets[0]['flags'].values())
        new_values = list(result[0]['flags'].values())
        self.assertNotEqual(original_values, new_values)


class TestR3RefreshSkip(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()

    def test_clean_state_returns_false(self):
        self.assertFalse(api._r3_should_skip())

    def test_dirty_state_low_roll_skips(self):
        helpers._quantum = 9000
        with mock.patch('random.random', return_value=0.01):
            self.assertTrue(api._r3_should_skip())

    def test_dirty_state_high_roll_runs(self):
        helpers._quantum = 9000
        with mock.patch('random.random', return_value=0.99):
            self.assertFalse(api._r3_should_skip())


class TestR4Freeze(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()

    def test_clean_state_no_sleep(self):
        with mock.patch('time.sleep') as sl:
            api._r4_maybe_freeze()
        sl.assert_not_called()

    def test_dirty_state_low_roll_sleeps(self):
        helpers._quantum = 9000
        with mock.patch('random.random', return_value=0.01), \
             mock.patch('time.sleep') as sl:
            api._r4_maybe_freeze()
        sl.assert_called_once()
        args, _ = sl.call_args
        self.assertGreaterEqual(args[0], 3.0)
        self.assertLessEqual(args[0], 8.0)

    def test_dirty_state_high_roll_no_sleep(self):
        helpers._quantum = 9000
        with mock.patch('random.random', return_value=0.99), \
             mock.patch('time.sleep') as sl:
            api._r4_maybe_freeze()
        sl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
