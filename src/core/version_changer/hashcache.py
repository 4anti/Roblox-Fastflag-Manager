"""B1-fast: an on-disk hash cache so a *repeat* 'Fix Roblox' run doesn't
re-read every already-present package just to confirm its checksum.

downloader.find_cached() verifies a cached package by reading the whole file and
computing its MD5. For a multi-hundred-MB Roblox build that re-hashing is the
dominant cost of a repeat fix (nothing is re-downloaded, but everything is
re-hashed). This cache records (size, mtime_ns, md5) per absolute file path; when
a file's size and mtime are unchanged, its MD5 is trusted without re-reading it.

Safety: a pure optimisation. Any miss, mismatch, or error falls back to a full
hash and refreshes the entry, so it can never return a wrong checksum.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

from src.utils.paths import user_data_dir

PATH = os.path.join(user_data_dir(), "download_hashcache.json")

_CHUNK = 1 << 16
# Lazy in-memory mirror of the on-disk cache: { abspath: [size, mtime_ns, md5] }.
_cache: Optional[Dict[str, List]] = None


def _load() -> Dict[str, List]:
    """Load the cache into memory once (best-effort); return the in-memory dict."""
    global _cache
    if _cache is None:
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = data if isinstance(data, dict) else {}
        except Exception:
            _cache = {}
    return _cache


def _save() -> None:
    """Persist the cache, dropping entries whose files no longer exist so the
    file can't grow unbounded across fixes that use throwaway temp paths."""
    global _cache
    if _cache is None:
        return
    try:
        pruned = {k: v for k, v in _cache.items() if os.path.exists(k)}
        _cache = pruned
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(pruned, f)
    except Exception:
        pass


def _compute_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def file_md5(path: str) -> Optional[str]:
    """Return the MD5 hex digest of `path`, reusing the cached value when the
    file's size and mtime are unchanged. Returns None if the file can't be read.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.abspath(path)
    cache = _load()
    entry = cache.get(key)
    if (entry and len(entry) == 3
            and entry[0] == st.st_size and entry[1] == st.st_mtime_ns):
        return entry[2]
    try:
        md5 = _compute_md5(path)
    except OSError:
        return None
    cache[key] = [st.st_size, st.st_mtime_ns, md5]
    _save()
    return md5
