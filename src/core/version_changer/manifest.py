"""Fetch and parse the Roblox package manifest ({guid}-rbxPkgManifest.txt).

Public format (Bloxstrap/Fishstrap): first line is the version header "v0",
then repeating 4-line records: filename / MD5-hex / packedSize / uncompressedSize.
The launcher exe is skipped (we install the player only).
"""

from __future__ import annotations

from typing import List, Optional

from src.utils.logger import log

_SUPPORTED_HEADER = "v0"
_SKIP_FILES = {"RobloxPlayerLauncher.exe"}
_TIMEOUT = 30


def manifest_url(cdn_base: str, guid: str) -> str:
    """Build the manifest URL for a CDN base (must end with '/') and build GUID."""
    return f"{cdn_base}{guid}-rbxPkgManifest.txt"


def parse_manifest(text: str) -> List[dict]:
    """Parse manifest text into a list of {name, md5, packed_size, size} dicts.

    Raises ValueError on an unsupported header or a truncated/short record.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or lines[0] != _SUPPORTED_HEADER:
        raise ValueError(f"unsupported manifest header: {lines[0] if lines else '<empty>'}")
    body = lines[1:]
    if len(body) % 4 != 0:
        raise ValueError("truncated manifest: records are not all 4 lines")
    packages: List[dict] = []
    for i in range(0, len(body), 4):
        name, md5, packed, size = body[i], body[i + 1], body[i + 2], body[i + 3]
        if name in _SKIP_FILES:
            continue
        try:
            packed_size = int(packed)
            uncompressed_size = int(size)
        except ValueError:
            raise ValueError(f"non-integer size in record for {name}")
        packages.append({
            "name": name,
            "md5": md5.lower(),
            "packed_size": packed_size,
            "size": uncompressed_size,
        })
    return packages


def fetch_manifest_text(cdn_base: str, guid: str) -> Optional[str]:
    """Fetch raw manifest text from one CDN base. Returns None on any failure."""
    try:
        import requests
        resp = requests.get(manifest_url(cdn_base, guid), timeout=_TIMEOUT,
                            headers={"User-Agent": "FFM-Version-Changer/1.0"})
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception as e:
        log(f"[!] Manifest fetch failed ({type(e).__name__})", (255, 200, 100))
        return None
