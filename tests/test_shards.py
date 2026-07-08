import unittest
from src.utils import helpers


class TestShardReconstruction(unittest.TestCase):
    def test_roundtrip_two_shards(self):
        secret = b'hello world'
        key = b'\x42' * len(secret)
        shard_a = bytes(s ^ k for s, k in zip(secret, key))
        shard_b = key
        self.assertEqual(helpers._unshard(shard_a, shard_b), secret)

    def test_roundtrip_three_shards(self):
        secret = b'\x01\x02\x03\x04'
        a = b'\xaa\xbb\xcc\xdd'
        b = b'\x55\x44\x33\x22'
        c = bytes(s ^ x ^ y for s, x, y in zip(secret, a, b))
        self.assertEqual(helpers._unshard(a, b, c), secret)

    def test_mismatched_length_returns_none(self):
        self.assertIsNone(helpers._unshard(b'\x01\x02', b'\x01'))

    def test_runtime_key_is_deterministic_within_process(self):
        k1 = helpers._runtime_key()
        k2 = helpers._runtime_key()
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 32)


if __name__ == '__main__':
    unittest.main()
