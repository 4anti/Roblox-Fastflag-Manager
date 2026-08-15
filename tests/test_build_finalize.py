import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import build_finalize


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


class TestBuildFinalizeHelpers(unittest.TestCase):
    def test_split_into_two_parts_xors_to_secret(self):
        secret = b'\x01\x02\x03\x04\x05\x06\x07\x08' * 4
        a, b = build_finalize.split_into_parts(secret, 2)
        result = bytes(x ^ y for x, y in zip(a, b))
        self.assertEqual(result, secret)
        self.assertEqual(len(a), len(secret))
        self.assertEqual(len(b), len(secret))

    def test_split_into_three_parts_xors_to_secret(self):
        secret = b'\xaa' * 32
        a, b, c = build_finalize.split_into_parts(secret, 3)
        result = bytes(x ^ y ^ z for x, y, z in zip(a, b, c))
        self.assertEqual(result, secret)

    def test_hash_first_n_bytes(self):
        path = os.path.join(FIXTURES, 'clean_polyfill.js')
        with open(path, 'rb') as f:
            expected = hashlib.sha256(f.read(1024)).digest()
        self.assertEqual(build_finalize.hash_first_n(path, 1024), expected)

    def test_hash_html_script_region(self):
        path = os.path.join(FIXTURES, 'clean_index.html')
        with open(path, 'rb') as f:
            data = f.read()
        idx = data.find(b'<script src="Sortable.min.js')
        region = data[max(0, idx-32):idx+224]
        self.assertEqual(build_finalize.hash_html_script_region(path),
                         hashlib.sha256(region).digest())


class TestBuildFinalizeRewrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, 'mod.py')
        with open(self.src, 'w') as f:
            f.write(
                'X_A = bytes(32)\n'
                'X_B = bytes(32)\n'
                'Y = 5\n'
            )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rewrite_replaces_exactly_one_placeholder(self):
        new = bytes(range(32))
        build_finalize.rewrite_placeholder(self.src, 'X_A', new)
        with open(self.src) as f:
            text = f.read()
        self.assertIn('X_A = bytes([0, 1, 2,', text)
        self.assertIn('X_B = bytes(32)', text)

    def test_rewrite_missing_var_raises(self):
        with self.assertRaises(RuntimeError):
            build_finalize.rewrite_placeholder(self.src, 'NOPE', b'\x00')

    def test_stamp_polyfill_full_writes_three_parts(self):
        with open(self.src, 'w') as f:
            f.write(
                'S2_A = bytes(32)\nS2_B = bytes(32)\nS2_C = bytes(32)\n'
            )
        path = os.path.join(FIXTURES, 'clean_polyfill.js')
        expected = build_finalize.hash_full(path)
        a, b, c = build_finalize.split_into_parts(expected, 3)
        build_finalize.rewrite_placeholder(self.src, 'S2_A', a)
        build_finalize.rewrite_placeholder(self.src, 'S2_B', b)
        build_finalize.rewrite_placeholder(self.src, 'S2_C', c)
        ns = {}
        with open(self.src) as f:
            exec(f.read(), ns)
        reconstructed = bytes(x ^ y ^ z for x, y, z in zip(
            ns['S2_A'], ns['S2_B'], ns['S2_C']))
        self.assertEqual(reconstructed, expected)

    def test_stamp_main_pyw_writes_two_parts(self):
        """S7: main.pyw hash → 2 parts stamped into flag_manager placeholders."""
        with open(self.src, 'w') as f:
            f.write('S7_A = bytes(32)\nS7_B = bytes(32)\n')
        # Synthesize a fake main.pyw payload; hash is what matters.
        fake_main = os.path.join(self.tmp, 'main.pyw')
        with open(fake_main, 'wb') as f:
            f.write(b'# fake main.pyw for test_stamp_main_pyw\nprint("hi")\n')
        expected = build_finalize.hash_full(fake_main)
        a, b = build_finalize.split_into_parts(expected, 2)
        build_finalize.rewrite_placeholder(self.src, 'S7_A', a)
        build_finalize.rewrite_placeholder(self.src, 'S7_B', b)
        ns = {}
        with open(self.src) as f:
            exec(f.read(), ns)
        reconstructed = bytes(x ^ y for x, y in zip(ns['S7_A'], ns['S7_B']))
        self.assertEqual(reconstructed, expected)


class TestBuildFinalizeValidation(unittest.TestCase):
    def test_compute_bytecode_hash_is_deterministic(self):
        def example():
            x = 1
            return x
        result = build_finalize.compute_bytecode_hash(example)
        self.assertEqual(len(result), 32)
        self.assertEqual(result, build_finalize.compute_bytecode_hash(example))


if __name__ == '__main__':
    unittest.main()
