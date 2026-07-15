"""Resolve the latest Roblox *production* player build GUID (version-xxxx).

Used by bootstrapper mode (Phase 4) and as a sanity source. Production channel
only — we never query other channels.
"""

from __future__ import annotations

import re
import time
import threading
from typing import Optional

from src.utils.logger import log

# Primary: structured JSON. Fallback: legacy plain-text version endpoint.
_CLIENT_VERSION_JSON = "https://clientsettingscdn.roblox.com/v2/client-version/WindowsPlayer"
_SETUP_VERSION_TEXT = "https://setup.rbxcdn.com/version"
_RX_VERSION = re.compile(r"^version-[0-9a-fA-F]+$")
_TIMEOUT = 10

# Resolver cache. Roblox ships a new production build every few days at
# most, so a positive result is safe to reuse for minutes. A NEGATIVE
# result is also cached briefly so a CDN outage doesn't get hammered
# once per second by the frontend status poll (`get_loading_status`).
# The log line is rate-limited to at most once per cooldown so the
# console doesn't fill with "x60/min" collapse counts during outages.
_CACHE_TTL_OK = 300.0     # 5 min on success
_CACHE_TTL_FAIL = 30.0    # 30 s on failure — back off, don't hammer
_LOG_COOLDOWN = 60.0      # one warn per minute max

_CACHE_LOCK = threading.Lock()
_CACHE = {'guid': None, 'ts': 0.0, 'last_log_ts': 0.0}


def _cache_reset():
    """Test-only: clear the resolver cache between cases."""
    with _CACHE_LOCK:
        _CACHE['guid'] = None
        _CACHE['ts'] = 0.0
        _CACHE['last_log_ts'] = 0.0


def _http_get_json(url: str) -> Optional[dict]:
    try:
        import requests
        resp = requests.get(url, timeout=_TIMEOUT,
                            headers={"User-Agent": "FFM-Version-Changer/1.0"})
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _http_get_text(url: str) -> Optional[str]:
    try:
        import requests
        resp = requests.get(url, timeout=_TIMEOUT,
                            headers={"User-Agent": "FFM-Version-Changer/1.0"})
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception:
        return None


def _valid(guid: Optional[str]) -> Optional[str]:
    if guid and _RX_VERSION.match(guid.strip()):
        return guid.strip()
    return None


def get_latest_production_guid() -> Optional[str]:
    """Return the latest production player build GUID (version-xxxx), or None.

    Cached under `_CACHE_LOCK`: a fresh positive result satisfies subsequent
    callers for `_CACHE_TTL_OK`; a fresh negative result throttles further
    HTTP attempts for `_CACHE_TTL_FAIL`. The failure log is coalesced to at
    most once per `_LOG_COOLDOWN`.
    """
    now = time.time()
    with _CACHE_LOCK:
        cached_guid = _CACHE['guid']
        age = now - _CACHE['ts']
        ttl = _CACHE_TTL_OK if cached_guid else _CACHE_TTL_FAIL
        # First call always misses (ts=0.0 -> age huge). Subsequent hits
        # inside the TTL return the cached value even when it's None.
        if _CACHE['ts'] > 0.0 and age < ttl:
            return cached_guid

    # Network work OUTSIDE the lock — 10 s timeouts must not block other
    # callers behind us.
    data = _http_get_json(_CLIENT_VERSION_JSON)
    guid = None
    if isinstance(data, dict):
        guid = _valid(data.get("clientVersionUpload"))
    if not guid:
        guid = _valid(_http_get_text(_SETUP_VERSION_TEXT))

    # Publish result. If two threads both raced past the cache and both
    # fetched, whichever grabs the lock last wins — either result is
    # equally valid (Roblox's CDN returns the same GUID for both).
    should_log = False
    with _CACHE_LOCK:
        _CACHE['guid'] = guid
        _CACHE['ts'] = now
        if guid is None and (now - _CACHE['last_log_ts']) >= _LOG_COOLDOWN:
            _CACHE['last_log_ts'] = now
            should_log = True
    if should_log:
        log("[!] Could not resolve latest production Roblox version",
            (255, 200, 100))
    return guid
