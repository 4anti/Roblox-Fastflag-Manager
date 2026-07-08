import unittest
from src.utils import helpers


class TestRotQuantum(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()

    def test_initial_value(self):
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_subtract_known_prime(self):
        helpers._rot_subtract(347)  # S1's prime
        self.assertEqual(helpers._rot_get(), 0xC06 - 347)

    def test_all_seven_primes_sum_to_quantum(self):
        for p in (347, 523, 419, 601, 283, 468, 437):
            helpers._rot_subtract(p)
        self.assertEqual(helpers._rot_get(), 0)

    def test_subtract_clamps_at_negative_max(self):
        for _ in range(20):
            helpers._rot_subtract(601)
        self.assertGreaterEqual(helpers._rot_get(), -0x10000)

    def test_dirty_predicate(self):
        helpers._rot_subtract(523)
        self.assertTrue(helpers._rot_is_dirty())
        helpers._rot_reset()
        for p in (347, 523, 419, 601, 283, 468, 437):
            helpers._rot_subtract(p)
        self.assertFalse(helpers._rot_is_dirty())


class TestQuantumBootstrap(unittest.TestCase):
    def setUp(self):
        helpers._rot_reset()

    def test_bootstrap_clean_starts_at_initial(self):
        helpers._rot_bootstrap(is_dirty=False)
        self.assertEqual(helpers._rot_get(), 0xC06)

    def test_bootstrap_dirty_starts_above_six_primes(self):
        helpers._rot_bootstrap(is_dirty=True)
        for p in (347, 523, 419, 601, 283, 468, 437):
            helpers._rot_subtract(p)
        self.assertNotEqual(helpers._rot_get(), 0)
        self.assertTrue(helpers._rot_is_dirty())


if __name__ == '__main__':
    unittest.main()
