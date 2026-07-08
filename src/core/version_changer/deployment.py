"""Resolve the latest Roblox *production* player build GUID (version-xxxx).

Used by bootstrapper mode (Phase 4) and as a sanity source. Production channel
only — we never query other channels.
"""

from __future__ import annotations

import re
from typing import Optional

from src.utils.logger import log

# Primary: structured JSON. Fallback: legacy plain-text version endpoint.
_CLIENT_VERSION_JSON = "https://clientsettingscdn.roblox.com/v2/client-version/WindowsPlayer"
_SETUP_VERSION_TEXT = "https://setup.rbxcdn.com/version"

_RX_VERSION = re.compile(r"^version-[0-9a-fA-F]+$")
_TIMEOUT = 10


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
    """Return the latest production player build GUID (version-xxxx), or None."""
    data = _http_get_json(_CLIENT_VERSION_JSON)
    if isinstance(data, dict):
        guid = _valid(data.get("clientVersionUpload"))
        if guid:
            return guid
    text = _http_get_text(_SETUP_VERSION_TEXT)
    guid = _valid(text)
    if guid:
        return guid
    log("[!] Could not resolve latest production Roblox version", (255, 200, 100))
    return None
