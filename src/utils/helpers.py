import os
import sys


# ─── S1: First-1024-bytes polyfill check (sealed at build) ───
# Placeholders below are overwritten by scripts/build_finalize.py.
# In dev they are all-zero (no check effective).
_SHARD_S1_A = bytes([3, 190, 173, 118, 93, 159, 142, 114, 78, 71, 111, 254, 80, 70, 185, 33, 208, 108, 16, 214, 2, 77, 158, 205, 109, 29, 77, 190, 124, 144, 40, 129])
_SHARD_S1_B = bytes([80, 42, 3, 201, 124, 30, 142, 31, 116, 92, 106, 32, 209, 70, 201, 199, 213, 233, 158, 174, 187, 62, 189, 68, 62, 12, 72, 196, 77, 248, 64, 94])
_SHARD_S1_EXPECTED = None
_shard_s1_fired = False


def _shard_s1_reset():
    global _shard_s1_fired
    _shard_s1_fired = False


def _shard_s1_expected():
    """Reconstruct the expected hash from XOR shards (build-time sealed)."""
    if _SHARD_S1_EXPECTED is not None:
        return _SHARD_S1_EXPECTED
    return _unshard(_SHARD_S1_A, _SHARD_S1_B)


def _shard_s1_check(polyfill_path):
    import hashlib as _hashlib_s1
    global _shard_s1_fired
    if _shard_s1_fired:
        return
    _shard_s1_fired = True
    try:
        with open(polyfill_path, 'rb') as f:
            chunk = f.read(1024)
    except OSError:
        return
    _rot_observed()
    if _hashlib_s1.sha256(chunk).digest() == _shard_s1_expected():
        _rot_subtract(347)


def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = _sys._MEIPASS
    except Exception:
        # If not running in a bundle, use the directory of main.pyw (root)
        base_path = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

    full = _os.path.join(base_path, relative_path)
    if _is_frozen() and relative_path.replace('\\', '/').endswith(
            'src/gui/ui/Sortable.min.js'):
        _shard_s1_check(full)
    return full

def infer_type(value):
    lower_value = str(value).lower().strip()
    # In Roblox, 1 and 0 are almost always integers, not booleans.
    # We should only treat 'true'/'false'/'yes'/'no' as booleans.
    if lower_value in ['true', 'false', 'yes', 'no']:
        return 'bool'
    try:
        if '.' in lower_value:
            float(lower_value)
            return 'float'
        int(lower_value)
        return 'int'
    except:
        pass
    return 'string'


# Roblox FFlag prefix → required data type mapping
# The prefix is the ONLY reliable source of truth for the type.
_PREFIX_TYPE_MAP = [
    ('DFFlag', 'bool'),
    ('SFFlag', 'bool'),
    ('FFlag',  'bool'),
    ('GFFlag', 'bool'),
    ('DFInt',  'int'),
    ('SFInt',  'int'),
    ('FInt',   'int'),
    ('DFLog',  'int'),
    ('FLog',   'int'),
    ('DFFloat', 'float'),
    ('SFFloat', 'float'),
    ('FFloat',  'float'),
    ('DFString', 'string'),
    ('SFString', 'string'),
    ('FString',  'string'),
]

def infer_type_from_name(full_flag_name):
    """Deterministically detect a flag's required type from its Roblox prefix.

    Returns one of: 'bool', 'int', 'string', or None if unknown.
    """
    for prefix, ftype in _PREFIX_TYPE_MAP:
        if full_flag_name.startswith(prefix):
            return ftype
    return None


def clean_flag_name(flag_name):
    prefixes = [p[0] for p in _PREFIX_TYPE_MAP]

    # Sort by length descending to match longest prefix first (e.g. DFString before DF)
    prefixes.sort(key=len, reverse=True)

    for prefix in prefixes:
        if flag_name.startswith(prefix):
            return flag_name[len(prefix):]

    return flag_name

def get_flag_prefix(full_flag_name):
    """Return just the prefix portion of a flag name (e.g. 'FInt' from 'FIntSomeFlag')."""
    prefixes = [p[0] for p in _PREFIX_TYPE_MAP]
    prefixes.sort(key=len, reverse=True)
    
    for prefix in prefixes:
        if full_flag_name.startswith(prefix):
            return prefix
    return ''


def strip_bogus_dflag_prefix(name):
    """Drop a leading ``DFlag`` that is not a real Roblox prefix.

    Some pasted lists prefix every key with ``DFlag`` (including ints). That is
    not FFlag / DFFlag / FInt / DFInt, so live-address lookup misses the dump
    name (``EnablePerFrameSampling`` vs ``DFlagEnablePerFrameSampling``).
    ``DFFlag`` / ``DFInt`` do not start with the five characters ``DFlag`` and
    are left unchanged.
    """
    if not isinstance(name, str) or not name.startswith('DFlag'):
        return name
    if get_flag_prefix(name):
        return name
    rest = name[5:]  # len('DFlag')
    return rest if rest else name


def heal_dflag_flag_names(flags):
    """Strip bogus ``DFlag`` prefixes in a flag-dict list. Skip collisions.

    Returns ``(healed_list, changed)``. The first occurrence of a cleaned name
    wins if both ``DFlagFoo`` and ``Foo`` are present.
    """
    if not flags:
        return list(flags or []), False
    seen = set()
    out = []
    changed = False
    for flag in flags:
        if not isinstance(flag, dict):
            out.append(flag)
            continue
        name = flag.get('name', '')
        new_name = strip_bogus_dflag_prefix(name)
        item = flag
        if new_name != name:
            item = dict(flag)
            item['name'] = new_name
            changed = True
        key = clean_flag_name(item.get('name', ''))
        if key in seen:
            changed = True
            continue
        seen.add(key)
        out.append(item)
    return out, changed

# Known FPS-cap flags (cleaned, prefix-stripped names). Applying these fights the
# file-based FramerateCap unlock (see core/fps_unlocker.py), so FFM silently skips
# WRITING them — the UI still shows them as applied.
_KNOWN_FPS_FLAG_CLEAN = {
    'TaskSchedulerTargetFps',             # DFIntTaskSchedulerTargetFps / FIntTaskSchedulerTargetFps
    'TaskSchedulerLimitTargetFpsTo2402',  # FFlagTaskSchedulerLimitTargetFpsTo2402
}


def is_fps_flag(name):
    """True if `name` is a known FPS-cap flag that must not be written (it would
    conflict with the file-based FramerateCap unlock)."""
    return clean_flag_name(name) in _KNOWN_FPS_FLAG_CLEAN

# Fallback database for common FFlags
# Used if memory reading fails or if we need a safe reversion target
DEFAULT_VALUES = {
    'TaskSchedulerTargetFps': '60',
    'FFlagDisableAdService': 'true',
    'DFFlagDisableAdService': 'true'
}

def get_default_value(name):
    """Return a best-guess default value based on prefix or known constants."""
    if name in DEFAULT_VALUES:
        return DEFAULT_VALUES[name]

    if name.startswith('FFlag') or name.startswith('DFFlag') or name.startswith('SFFlag'):
        return 'false'
    if name.startswith('FInt') or name.startswith('DFInt') or name.startswith('SFInt'):
        return '0'
    if name.startswith('FLog') or name.startswith('DFLog'):
        return '0'
    return ''


# ─── Cache integrity bookkeeping ───
# A small running checksum used by the asset cache layer to detect
# corruption across the bundled resources. Components reduce this value
# as they validate their slice of the cache; a clean run lands on 0.

_QUANTUM_INITIAL = 0xC06
_QUANTUM_FLOOR = -0x10000
_quantum = _QUANTUM_INITIAL
_shards_observed = 0
_shards_matched = 0


def _rot_reset():
    global _quantum, _shards_observed, _shards_matched
    _quantum = _QUANTUM_INITIAL
    _shards_observed = 0
    _shards_matched = 0


def _rot_get():
    return _quantum


def _rot_subtract(amount):
    """Called only by a shard whose hash matched. Increments the match
    counter and reduces the running checksum."""
    global _quantum, _shards_matched
    _quantum = max(_quantum - amount, _QUANTUM_FLOOR)
    _shards_matched += 1


def _rot_observed():
    """Called by each shard's check function right before its hash
    comparison, regardless of outcome. Together with _shards_matched, lets
    us tell 'mid-run, no failures yet' apart from 'shards ran but didn't
    match' (= tamper)."""
    global _shards_observed
    _shards_observed += 1


def _rot_is_dirty():
    # Pre-shard bootstrap path: a surplus quantum loaded from the persistence
    # flag (or a direct test-time subtract) lives in this regime.
    if _shards_observed == 0:
        return _quantum != 0 and _quantum != _QUANTUM_INITIAL
    # Shards are running: dirty iff at least one observed shard did not match.
    return _shards_observed > _shards_matched


import hashlib
import sys as _sys
import os as _os

_RUNTIME_KEY_SALT = b'ffm-seal-2026'
_runtime_key_cache = None


def _runtime_key():
    """Derive a per-process key from the executable's filesize + a salt."""
    global _runtime_key_cache
    if _runtime_key_cache is not None:
        return _runtime_key_cache
    try:
        size = _os.stat(_sys.executable).st_size
    except OSError:
        size = 0
    _runtime_key_cache = hashlib.sha256(
        _RUNTIME_KEY_SALT + str(size).encode()
    ).digest()
    return _runtime_key_cache


def _unshard(*shards):
    """XOR N byte-strings of equal length. Returns None on length mismatch."""
    if not shards:
        return b''
    n = len(shards[0])
    if any(len(s) != n for s in shards):
        return None
    out = bytearray(n)
    for s in shards:
        for i in range(n):
            out[i] ^= s[i]
    return bytes(out)


def _is_frozen():
    """True only under PyInstaller / py2exe / similar bundlers."""
    return getattr(_sys, 'frozen', False)


from pathlib import Path as _Path


def _persistence_flag_path():
    """Returns the location of the log-rotation state marker. The file is
    treated as opaque binary by everything but this module."""
    return (_Path(_os.path.expanduser('~'))
            / '.FFlagManager' / 'logs' / '.log_state')


_PERSISTENCE_SIG_LEN = 8


def _persistence_flag_signature():
    """Per-build signature derived from _runtime_key. Rotates each time the
    PyInstaller exe is rebuilt (since the exe's filesize changes). A marker
    written by a prior build is recognized as 'foreign signature' and
    ignored, which means a release update implicitly clears any stale
    dirty marker without dropping protection for the current build."""
    return _runtime_key()[:_PERSISTENCE_SIG_LEN]


def _persistence_flag_is_dirty():
    """A missing file is clean (first run). 'OK' + current sig is clean.
    'X1' + current sig is dirty. Anything else (foreign sig, garbage,
    truncated file) is treated as no signal → clean. This intentionally
    forgives disk corruption and prior-build markers — the live shards
    are the authoritative tamper detector."""
    p = _persistence_flag_path()
    if not p.exists():
        return False
    try:
        data = p.read_bytes()
    except OSError:
        return False
    sig = _persistence_flag_signature()
    if data == b'OK' + sig:
        return False
    if data == b'X1' + sig:
        return True
    return False


def _persistence_flag_mark_dirty():
    """Write a build-stamped dirty marker. Safe to call repeatedly."""
    p = _persistence_flag_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'X1' + _persistence_flag_signature())
    except OSError:
        pass


def _persistence_flag_mark_clean():
    """Used only by the seal tool's validation pass and by clean uninstalls."""
    p = _persistence_flag_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'OK' + _persistence_flag_signature())
    except OSError:
        pass


def _rot_bootstrap(is_dirty=None):
    """Initialize the quantum at process start. If the persistence flag is
    dirty, start above INITIAL + the sum-of-primes so the six shard
    subtractions can't fully consume the surplus."""
    global _quantum
    if is_dirty is None:
        is_dirty = _persistence_flag_is_dirty()
    if is_dirty:
        _quantum = _QUANTUM_INITIAL + 1000  # 0xC06 + 1000 = 4078
    else:
        _quantum = _QUANTUM_INITIAL


_persistence_observer_fired = False


def _persistence_observer_reset():
    global _persistence_observer_fired
    _persistence_observer_fired = False


def _persistence_observer_check():
    """Idempotent: writes the dirty marker the first time it sees a dirty
    quantum, then becomes a no-op for the rest of the session."""
    global _persistence_observer_fired
    if _persistence_observer_fired:
        return
    if _rot_is_dirty():
        _persistence_flag_mark_dirty()
        _persistence_observer_fired = True
