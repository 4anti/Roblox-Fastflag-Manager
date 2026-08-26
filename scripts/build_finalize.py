"""Build finalizer: computes the asset-cache checksums for the shipped UI
assets and stamps them into the packaged modules so the runtime cache
validation can reconstruct the expected values.

Reads the bundled UI assets only — never modifies them.

Run after dependencies are installed and before packaging:
    python scripts/build_finalize.py --project .
"""

import argparse
import hashlib
import os
import re
import secrets
import sys


def hash_full(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).digest()


def hash_first_n(path, n):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read(n)).digest()


def hash_html_script_region(path):
    with open(path, 'rb') as f:
        data = f.read()
    idx = data.find(b'<script src="Sortable.min.js')
    if idx < 0:
        raise ValueError(f'script tag not found in {path}')
    region = data[max(0, idx - 32):idx + 224]
    return hashlib.sha256(region).digest()


def split_into_parts(secret, n):
    """Returns n byte-strings of len(secret) whose XOR equals secret."""
    assert n >= 1
    parts = [secrets.token_bytes(len(secret)) for _ in range(n - 1)]
    last = bytearray(secret)
    for s in parts:
        for i in range(len(secret)):
            last[i] ^= s[i]
    parts.append(bytes(last))
    return parts


def bytes_to_python_literal(b):
    return 'bytes([' + ', '.join(str(x) for x in b) + '])'


def rewrite_placeholder(source_path, var_name, new_value):
    """Find `var_name = bytes(32)` or `var_name = bytes([...])` and replace
    the right-hand side with the new value."""
    with open(source_path, 'r', encoding='utf-8') as f:
        text = f.read()
    pattern = re.compile(
        r'^(' + re.escape(var_name) + r'\s*=\s*)(bytes\([^)]*\))',
        re.MULTILINE
    )
    new_text, count = pattern.subn(
        lambda m: m.group(1) + bytes_to_python_literal(new_value), text
    )
    if count != 1:
        raise RuntimeError(
            f'{var_name} not found exactly once in {source_path}')
    with open(source_path, 'w', encoding='utf-8') as f:
        f.write(new_text)


def compute_bytecode_hash(fn):
    return hashlib.sha256(fn.__code__.co_code).digest()


def _rewrite_api_hmac_part_b(api_path, shard):
    with open(api_path, 'r', encoding='utf-8') as f:
        text = f.read()
    pattern = re.compile(
        r'(_cfg_module\._register_hmac_shard_b\()bytes\([^)]*\)(\))',
        re.MULTILINE
    )
    new_text, count = pattern.subn(
        lambda m: m.group(1) + bytes_to_python_literal(shard) + m.group(2),
        text
    )
    if count != 1:
        raise RuntimeError('_register_hmac_shard_b call site not found')
    with open(api_path, 'w', encoding='utf-8') as f:
        f.write(new_text)


def finalize_project(project_root):
    """Computes checksums and stamps every placeholder in the source tree."""
    p = os.path.join

    js_path = p(project_root, 'src', 'gui', 'ui', 'Sortable.min.js')
    html_path = p(project_root, 'src', 'gui', 'ui', 'index.html')
    main_pyw_path = p(project_root, 'main.pyw')

    helpers_py = p(project_root, 'src', 'utils', 'helpers.py')
    config_py = p(project_root, 'src', 'utils', 'config.py')
    logger_py = p(project_root, 'src', 'utils', 'logger.py')
    main_py = p(project_root, 'src', 'gui', 'main_window.py')
    api_py = p(project_root, 'src', 'gui', 'api.py')
    preset_py = p(project_root, 'src', 'core', 'preset_manager.py')
    flag_mgr_py = p(project_root, 'src', 'core', 'flag_manager.py')

    # S1: first 1024 bytes of polyfill, 2 parts
    s1_hash = hash_first_n(js_path, 1024)
    s1a, s1b = split_into_parts(s1_hash, 2)
    rewrite_placeholder(helpers_py, '_SHARD_S1_A', s1a)
    rewrite_placeholder(helpers_py, '_SHARD_S1_B', s1b)

    # S2: full polyfill, 3 parts
    s2_hash = hash_full(js_path)
    s2a, s2b, s2c = split_into_parts(s2_hash, 3)
    rewrite_placeholder(main_py, '_SHARD_S2_A', s2a)
    rewrite_placeholder(main_py, '_SHARD_S2_B', s2b)
    rewrite_placeholder(main_py, '_SHARD_S2_C', s2c)

    # S4: index.html script region, 2 parts
    s4_hash = hash_html_script_region(html_path)
    s4a, s4b = split_into_parts(s4_hash, 2)
    rewrite_placeholder(preset_py, '_SHARD_S4_A', s4a)
    rewrite_placeholder(preset_py, '_SHARD_S4_B', s4b)

    # S6: same as S2 (full polyfill), independent parts
    s6a, s6b = split_into_parts(s2_hash, 2)
    rewrite_placeholder(logger_py, '_SHARD_S6_A', s6a)
    rewrite_placeholder(logger_py, '_SHARD_S6_B', s6b)
    # S7: full main.pyw checksum, 2 parts. main.pyw is gitignored in the
    # public repo and shipped inside the exe as bundled data (--add-data).
    # This part detects a rebuild against a different main.pyw (e.g. a newbie
    # who reconstructs a stub after cloning the public repo).
    if os.path.isfile(main_pyw_path):
        s7_hash = hash_full(main_pyw_path)
        s7a, s7b = split_into_parts(s7_hash, 2)
        rewrite_placeholder(flag_mgr_py, '_SHARD_S7_A', s7a)
        rewrite_placeholder(flag_mgr_py, '_SHARD_S7_B', s7b)
    else:
        print('WARNING: main.pyw not found — skipping S7 stamp (will remain '
              'no-op placeholder). Restore main.pyw before releasing.')

    # HMAC key: 32 random bytes, A in config.py + B in api.py
    hmac_key = secrets.token_bytes(32)
    ka, kb = split_into_parts(hmac_key, 2)
    rewrite_placeholder(config_py, '_HMAC_SHARD_A', ka)
    _rewrite_api_hmac_part_b(api_py, kb)

    print('First-pass finalize complete.')


def finalize_s5(project_root, api_py):
    """Second pass: re-import main_window after S2 was stamped, hash its
    __init__.co_code, and stamp that into api.py's _SHARD_S5_A/B."""
    sys.path.insert(0, project_root)
    try:
        for mod in list(sys.modules):
            if mod.startswith('src.gui.main_window'):
                del sys.modules[mod]
        from src.gui import main_window
        co_hash = compute_bytecode_hash(main_window.MainWindow.__init__)
    finally:
        if sys.path and sys.path[0] == project_root:
            sys.path.pop(0)

    a, b = split_into_parts(co_hash, 2)
    rewrite_placeholder(api_py, '_SHARD_S5_A', a)
    rewrite_placeholder(api_py, '_SHARD_S5_B', b)


def validate(project_root):
    """Smoke test: import the stamped modules, run the gates, and assert
    _rot_quantum == 0 after exercising every check."""
    sys.path.insert(0, project_root)
    try:
        for mod in list(sys.modules):
            if mod.startswith('src.'):
                del sys.modules[mod]
        from src.utils import helpers
        from src.gui import main_window, api
        from src.core import preset_manager, flag_manager
        from src.utils import logger as logger_mod, config

        import unittest.mock as mock
        with mock.patch.object(helpers, '_is_frozen', return_value=True):
            helpers._rot_reset()
            helpers._shard_s1_reset()
            main_window._shard_s2_reset()
            preset_manager._shard_s4_reset()
            api._shard_s5_reset()
            logger_mod._shard_s6_reset()
            flag_manager._shard_s7_reset()
            helpers.get_resource_path('src/gui/ui/Sortable.min.js')
            main_window._shard_s2_check()
            preset_manager._shard_s4_check()
            api._shard_s5_check()
            logger_mod._shard_s6_tick()
            flag_manager._shard_s7_check()
            import tempfile
            from pathlib import Path
            config.Config.APP_DIR = Path(tempfile.mkdtemp())
            config.Config.SETTINGS_FILE = config.Config.APP_DIR / 'settings.json'
            config.Config.save_settings({'ads_enabled': True})
            a_obj = api.Api.__new__(api.Api)
            a_obj.settings = config.Config.load_settings()
            a_obj.get_content_filter()
            if helpers._rot_get() != 0:
                raise SystemExit(
                    f'Validation failed: quantum={helpers._rot_get()}')
        print('Validation pass: quantum landed on 0. Build is shippable.')
    finally:
        if sys.path and sys.path[0] == project_root:
            sys.path.pop(0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', required=True,
                    help='Path to the project root (use "." in CI)')
    ap.add_argument('--skip-validate', action='store_true')
    args = ap.parse_args()
    finalize_project(args.project)
    finalize_s5(args.project,
                os.path.join(args.project, 'src', 'gui', 'api.py'))
    if not args.skip_validate:
        validate(args.project)
