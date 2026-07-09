"""Download Roblox packages to a staging dir with checksum verification.

Per package: reuse an already-present copy (correct MD5) from the existing
install/cache when possible; otherwise fetch from the weighted CDN list with
retries and HTTPS->HTTP fallback. Nothing is committed here — see installer.py.
"""

from __future__ import annotations

import hashlib
import os
from typing import Callable, List, Optional

from src.utils.logger import log

# Weighted CDN bases (must end with '/'). Tried in order per package.
#
# Every entry serves the same S3-backed object under `*.rbxcdn.com` (verified
# by matching `x-amz-version-id` headers), so a checksum mismatch across
# entries would imply someone MITM'd the TLS handshake — the manifest MD5
# verify at download_package() would then catch it and force a retry.
#
# `roblox-setup.cachefly.net` used to serve here but Roblox retired it —
# hitting it now returns "Hostname not configured". Removed to avoid one
# guaranteed-404 round-trip per download attempt.
CDN_BASES = [
    "https://setup.rbxcdn.com/",
    "https://setup-aws.rbxcdn.com/",
    "https://setup-ak.rbxcdn.com/",
    "https://s3.amazonaws.com/setup.roblox.com/",
]
_MAX_RETRIES = 5
_TIMEOUT = 120
_CHUNK = 1 << 16


def verify_md5(path: str, expected_md5: str) -> bool:
    """True if the file at path exists and its MD5 matches expected (case-insensitive)."""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest().lower() == expected_md5.lower()
    except OSError:
        return False


def find_cached(package: dict, search_dirs: List[str]) -> Optional[str]:
    """Return the path of an existing copy of this package with the correct MD5
    in any of search_dirs, or None. Enables dedupe vs the stock Roblox cache.

    Routes the checksum through the on-disk hash cache so a repeat fix verifies
    unchanged packages with a stat() instead of re-reading the whole file.
    """
    from src.core.version_changer import hashcache
    expected = package["md5"].lower()
    for d in search_dirs:
        candidate = os.path.join(d, package["name"])
        if os.path.isfile(candidate):
            actual = hashcache.file_md5(candidate)
            if actual is not None and actual.lower() == expected:
                return candidate
    return None


def _package_url(cdn_base: str, guid: str, name: str) -> str:
    return f"{cdn_base}{guid}-{name}"


def download_package(package: dict, guid: str, staging_dir: str,
                     progress_cb: Optional[Callable[[int, int], None]] = None) -> Optional[str]:
    """Download one package into staging_dir and verify its MD5. Returns the
    staged file path on success, or None after exhausting all CDNs/retries.
    progress_cb(downloaded_bytes, total_bytes) is called during transfer."""
    import requests
    dest = os.path.join(staging_dir, package["name"])
    for attempt in range(_MAX_RETRIES):
        cdn = CDN_BASES[attempt % len(CDN_BASES)]
        url = _package_url(cdn, guid, package["name"])
        try:
            r = requests.get(url, stream=True, timeout=_TIMEOUT,
                            headers={"User-Agent": "FFM-Version-Changer/1.0"})
            if r.status_code != 200:
                continue
            total = int(r.headers.get("content-length", 0))
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=_CHUNK):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb and total:
                            progress_cb(done, total)
            if verify_md5(dest, package["md5"]):
                return dest
            log(f"[!] {package['name']}: checksum mismatch, retrying", (255, 200, 100))
        except Exception:
            continue
    log(f"[!] {package['name']}: all download attempts failed", (255, 100, 100))
    return None
