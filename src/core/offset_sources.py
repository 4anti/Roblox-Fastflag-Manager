"""Individual offset-fetch sources. Each function returns Optional[bytes]
on success, None on any failure. Source ID constants identify which path
provided the bytes for telemetry/logging.

The fetch chain is orchestrated in offset_loader.py; this module deliberately
contains no chain logic or validation beyond size capping.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

from src.utils.logger import log
from src.utils.paths import resource_path


# Source IDs (used for telemetry in offset_loader)
SRC_IMTHEO_DEV_REQUESTS = "imtheo_dev_requests"
SRC_IMTHEO_DEV_CURL = "imtheo_dev_curl"
SRC_IMTHEO_REQUESTS = "imtheo_requests"
SRC_IMTHEO_CURL = "imtheo_curl"
SRC_WORKERS_DEV_REQUESTS = "workers_dev_requests"
SRC_WORKERS_DEV_CURL = "workers_dev_curl"
SRC_GITHUB_REQUESTS = "github_requests"
SRC_GITHUB_CURL = "github_curl"
SRC_DISK_CACHE = "disk_cache"
SRC_BUNDLED = "bundled_baseline"

# Direct-fetch dumpers other than imtheo (added for the 4-tier chain)
SRC_SOULOVERYALL_REQUESTS = "souloveryall_requests"
SRC_SOULOVERYALL_CURL = "souloveryall_curl"
SRC_NTRVM_REQUESTS = "ntrvm_requests"
SRC_NTRVM_CURL = "ntrvm_curl"
# Our GitHub-hosted mirror of the same endpoints (raw.githubusercontent)
SRC_MIRROR_IMTHEO_REQUESTS = "mirror_imtheo_requests"
SRC_MIRROR_IMTHEO_CURL = "mirror_imtheo_curl"
SRC_MIRROR_WORKERS_REQUESTS = "mirror_workers_requests"
SRC_MIRROR_WORKERS_CURL = "mirror_workers_curl"
SRC_MIRROR_NTRVM_REQUESTS = "mirror_ntrvm_requests"
SRC_MIRROR_NTRVM_CURL = "mirror_ntrvm_curl"
SRC_MIRROR_SOULOVERYALL_REQUESTS = "mirror_souloveryall_requests"
SRC_MIRROR_SOULOVERYALL_CURL = "mirror_souloveryall_curl"

# URLs — primary sources (different hosts, all serve FFlag offsets)
IMTHEO_DEV_FFLAGS_HPP = "https://dev.imtheo.lol/Offsets/FFlags.hpp"   # preferred (latest dumper)
IMTHEO_FFLAGS_HPP = "https://offsets.imtheo.lol/FFlags.hpp"          # current stable mirror (same format/content as dev)
WORKERS_DEV_FFLAGS_HPP = "https://offsets.ntgetwritewatch.workers.dev/FFlags.hpp"  # alt mirror (different format, no version embed)

# Our own GitHub mirror — last resort before disk/bundled
GITHUB_MIRROR_FFLAGS_HPP = (
    "https://raw.githubusercontent.com/4anti/Roblox-Fastflag-Manager/main/data/FFlags.hpp"
)

# Other direct dumpers
SOULOVERYALL_OFFSETS_HPP = "https://raw.githubusercontent.com/souloveryall/offsets.hpp/main/Offsets.hpp"
NTRVM_FFLAGS_HPP = "https://raw.githubusercontent.com/NtReadVirtualMemory/Roblox-Offsets-Website/main/FFlags.hpp"
# Our GitHub-hosted mirror (Roblox-Offsets-ALL). These URLs 404 while the
# repo is private; the loader chain treats a 404 the same as any other failure
# and falls through, so keeping them here is forward-safe. Flip the repo to
# public via `gh repo edit 4anti/Roblox-Offsets-ALL --visibility public
# --accept-visibility-change-consequences` once analysis is done.
MIRROR_BASE = "https://raw.githubusercontent.com/4anti/Roblox-Offsets-ALL/main/endpoints"
MIRROR_IMTHEO_FFLAGS_HPP     = MIRROR_BASE + "/imtheo/FFlags.hpp"
MIRROR_WORKERS_FFLAGS_HPP    = MIRROR_BASE + "/workers-dev/FFlags.hpp"
MIRROR_NTRVM_FFLAGS_HPP      = MIRROR_BASE + "/ntreadvirtualmemory/FFlags.hpp"
MIRROR_SOULOVERYALL_HPP      = MIRROR_BASE + "/souloveryall/Offsets.hpp"

# Ordered chain of (source_id_requests, source_id_curl, url) tuples consumed by
# the loader. Tiered per the FFM 4-source policy:
#   Tier 1: imtheo direct (primary + stable)
#   Tier 2: imtheo GitHub mirror (our Roblox-Offsets-ALL repo)
#   Tier 3: other direct dumpers (souloveryall, NtReadVirtualMemory, workers.dev)
#   Tier 4: other GitHub mirrors (Roblox-Offsets-ALL copies of Tier 3 sources)
#   Tail:   legacy FFM data/FFlags.hpp GitHub mirror (kept for existing-user builds)
# Each URL is attempted via Python requests first, then via curl.exe (Windows
# native SSL) before falling through to the next URL.
PRIMARY_NETWORK_SOURCES = [
    # Tier 1 — imtheo direct
    (SRC_IMTHEO_DEV_REQUESTS,           SRC_IMTHEO_DEV_CURL,           IMTHEO_DEV_FFLAGS_HPP),
    (SRC_IMTHEO_REQUESTS,               SRC_IMTHEO_CURL,               IMTHEO_FFLAGS_HPP),
    # Tier 2 — imtheo GitHub mirror we made
    (SRC_MIRROR_IMTHEO_REQUESTS,        SRC_MIRROR_IMTHEO_CURL,        MIRROR_IMTHEO_FFLAGS_HPP),
    # Tier 3 — other direct dumpers
    (SRC_SOULOVERYALL_REQUESTS,         SRC_SOULOVERYALL_CURL,         SOULOVERYALL_OFFSETS_HPP),
    (SRC_NTRVM_REQUESTS,                SRC_NTRVM_CURL,                NTRVM_FFLAGS_HPP),
    (SRC_WORKERS_DEV_REQUESTS,          SRC_WORKERS_DEV_CURL,          WORKERS_DEV_FFLAGS_HPP),
    # Tier 4 — other GitHub mirrors we made
    (SRC_MIRROR_SOULOVERYALL_REQUESTS,  SRC_MIRROR_SOULOVERYALL_CURL,  MIRROR_SOULOVERYALL_HPP),
    (SRC_MIRROR_NTRVM_REQUESTS,         SRC_MIRROR_NTRVM_CURL,         MIRROR_NTRVM_FFLAGS_HPP),
    (SRC_MIRROR_WORKERS_REQUESTS,       SRC_MIRROR_WORKERS_CURL,       MIRROR_WORKERS_FFLAGS_HPP),
    # Tail — legacy FFM-internal mirror (existing shipped chain, do not remove)
    (SRC_GITHUB_REQUESTS,               SRC_GITHUB_CURL,               GITHUB_MIRROR_FFLAGS_HPP),
]

# Bundled baseline (shipped inside _MEIPASS via PyInstaller --add-data=src/data)
BUNDLED_BASELINE_PATH = resource_path(os.path.join("src", "data", "FFlags_baseline.hpp"))

# 5 MB cap shared across sources (defensive — body is regex-scanned downstream)
MAX_BYTES = 5 * 1024 * 1024

# Per-source timeouts
REQUESTS_TIMEOUT = 10
CURL_TIMEOUT = 15

# Windows: suppress console-window flash when spawning curl
_SUBPROCESS_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _host(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url


from urllib.parse import urlparse
import os as _os

_MIRROR_REPO_PATH_PREFIX = "/4anti/Roblox-Offsets-ALL/main/endpoints/"


def _mirror_meta_verified(url: str) -> bool:
    """Gate for Tier 2/4 mirror URLs. Fetches the sibling meta.json and returns
    True only if it parses AND verified == True. Returns True (bypass) for any
    URL that is NOT one of our mirror URLs, so Tier 1/3/tail sources are
    unaffected. Fail-safe: any error path returns False so an un-attested
    mirror body is never consumed. Honors the FFM_TRUST_MIRROR=1 env var as a
    one-session override.
    """
    if _os.environ.get("FFM_TRUST_MIRROR") == "1":
        return True
    try:
        p = urlparse(url)
        if p.netloc != "raw.githubusercontent.com":
            return True
        if not p.path.startswith(_MIRROR_REPO_PATH_PREFIX):
            return True
        parent = p.path.rsplit("/", 1)[0]
        meta_url = f"https://{p.netloc}{parent}/meta.json"
        import requests as _r
        resp = _r.get(meta_url, timeout=REQUESTS_TIMEOUT)
        if resp.status_code != 200:
            return False
        return resp.json().get("verified") is True
    except Exception:
        return False


def fetch_via_requests(url: str) -> Optional[bytes]:
    """Fetch via Python requests. Returns bytes on 200 + size-ok, else None."""
    if not _mirror_meta_verified(url):
        return None
    if not url.startswith("https://"):
        return None
    try:
        import requests
    except ImportError:
        return None

    host = _host(url)
    try:
        resp = requests.get(
            url,
            timeout=REQUESTS_TIMEOUT,
            stream=True,
            headers={"User-Agent": "FFM-Offset-Loader/1.0"},
        )
    except Exception as e:
        log(f"[!] {host} via requests: unreachable ({type(e).__name__})", (255, 200, 100))
        return None

    if resp.status_code != 200:
        log(f"[!] {host} via requests: HTTP {resp.status_code}", (255, 200, 100))
        resp.close()
        return None

    body = bytearray()
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                log(f"[!] {host} via requests: exceeded {MAX_BYTES}B cap", (255, 100, 100))
                resp.close()
                return None
    except Exception as e:
        log(f"[!] {host} via requests: read error ({type(e).__name__})", (255, 200, 100))
        resp.close()
        return None
    resp.close()
    return bytes(body)


def fetch_via_curl(url: str) -> Optional[bytes]:
    """Fetch via system curl.exe. Uses Windows native SSL (schannel) which
    succeeds in many cases where Python's OpenSSL is blocked by antivirus
    SSL interception or has a TLS version mismatch.
    """
    if not _mirror_meta_verified(url):
        return None
    curl_path = shutil.which("curl")
    if not curl_path:
        return None
    host = _host(url)
    try:
        result = subprocess.run(
            [curl_path, "-fsSL", "--max-time", str(CURL_TIMEOUT), "-A", "FFM-Offset-Loader/1.0", url],
            capture_output=True,
            timeout=CURL_TIMEOUT + 5,
            creationflags=_SUBPROCESS_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        log(f"[!] {host} via curl: timeout", (255, 200, 100))
        return None
    except Exception as e:
        log(f"[!] {host} via curl: spawn failed ({type(e).__name__})", (255, 200, 100))
        return None

    if result.returncode != 0:
        log(f"[!] {host} via curl: exit {result.returncode}", (255, 200, 100))
        return None
    body = result.stdout or b""
    if not body:
        return None
    if len(body) > MAX_BYTES:
        log(f"[!] {host} via curl: exceeded {MAX_BYTES}B cap", (255, 100, 100))
        return None
    return body


def read_bundled_baseline() -> Optional[bytes]:
    """Read the FFlags.hpp baseline shipped inside the .exe. Last-resort
    source so first-run users with no network always get something.
    """
    try:
        if not os.path.isfile(BUNDLED_BASELINE_PATH):
            return None
        with open(BUNDLED_BASELINE_PATH, "rb") as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            log(f"[!] bundled baseline: exceeds {MAX_BYTES}B cap", (255, 100, 100))
            return None
        return data
    except Exception as e:
        log(f"[!] bundled baseline read failed: {type(e).__name__}", (255, 200, 100))
        return None
