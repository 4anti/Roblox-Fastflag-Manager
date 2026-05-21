"""Centralized FFlag offset loader with multi-source fallback chain.

Sources tried in order (first valid wins). Each network URL is attempted
via Python requests first, then via system curl.exe (bypasses AV SSL
interception):
  1. dev.imtheo.lol      (preferred — latest dumper)
  2. offsets.imtheo.lol  (stable mirror — same format/content as dev)
  3. offsets.ntgetwritewatch.workers.dev (alt mirror — Format B)
  4. GitHub mirror (data/FFlags.hpp)
  5. Disk cache (~/.FFlagManager/offsets_cache.json)
  6. Bundled baseline shipped with the .exe

Defensive parsing: treat remote body as untrusted (size cap, regex, RVA ranges,
ASCII names). A body is only treated as "valid" if it parses to at least
MIN_VALID_FLAGS entries AND has a FFlagList.Pointer struct offset — this
prevents AV captive portals and proxy error pages (which return 200 OK HTML)
from poisoning the disk cache.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import Optional

from src.utils.logger import log
from src.utils.helpers import clean_flag_name, infer_type_from_name
from src.utils.paths import user_data_dir
from src.core import offset_sources
from src.core.offset_sources import (
    PRIMARY_NETWORK_SOURCES,
    SRC_DISK_CACHE,
    SRC_BUNDLED,
)


# ───────────────────────── paths ─────────────────────────

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_PATH = os.path.join(user_data_dir(), "offsets_cache.json")
_LEGACY_CACHE_PATH = os.path.join(_REPO_ROOT, "offsets_cache.json")


# ───────────────────────── safety constants ─────────────────────────

IMTHEO_MAX_BYTES = 5 * 1024 * 1024

RVA_MIN = 0x100000
RVA_MAX = 0x10000000

STRUCT_OFF_MIN = 0x100000
STRUCT_OFF_MAX = 0x10000000

# Captive-portal / error-page guard. A real FFlags.hpp has thousands of entries;
# anything less than this threshold is almost certainly a bad fetch.
MIN_VALID_FLAGS = 500

# Matches BOTH formats — the `inline\s+constexpr\s+` prefix is optional so this
# captures the imtheo `inline constexpr uintptr_t NAME = 0x...` lines AND the
# workers.dev mirror's `uintptr_t NAME = 0x...` lines.
_RX_UINTPTR = re.compile(
    rb'(?:inline\s+constexpr\s+)?uintptr_t\s+([A-Za-z_][A-Za-z0-9_]{0,127})\s*=\s*0x([0-9a-fA-F]{1,8})'
)

# Format A (imtheo / dev.imtheo): nested `namespace FFlagList { ... }` block.
_RX_FFLAGLIST_NAMESPACE = re.compile(
    rb'namespace\s+FFlagList\s*\{([\s\S]{0,65536}?)\}', re.MULTILINE
)

# Format B (workers.dev): `namespace FFlagOffsets { uintptr_t FFlagList = ...; uintptr_t ValueGetSet = ...; uintptr_t FlagToValue = ...; }`
# We capture the FFlagOffsets body to find the three sibling vars.
_RX_FFLAGOFFSETS_NAMESPACE = re.compile(
    rb'namespace\s+FFlagOffsets\s*\{([\s\S]{0,65536}?)\}', re.MULTILINE
)

# Format B struct member name -> canonical (Format A) name
_STRUCT_ALIASES = {
    "FFlagList": "Pointer",       # the actual pointer offset
    "ValueGetSet": "ToFlag",       # Format A calls it ToFlag
    "FlagToValue": "ToValue",      # Format A calls it ToValue
}

# Format A only: embedded ClientVersion string + header `Roblox Version:` line.
# Format B (workers.dev) has neither — version returns None for those sources.
_RX_CLIENT_VERSION = re.compile(rb'ClientVersion\s*=\s*"([^"]+)"')
_RX_HEADER_VERSION = re.compile(rb'Roblox Version\s*:\s*(version-[0-9a-fA-F]+)')


# ───────────────────────── module cache ─────────────────────────

_session_cache: Optional[dict] = None
_last_source_id: Optional[str] = None
_last_baseline_stale: bool = False


def reset_cache():
    global _session_cache, _last_source_id, _last_baseline_stale
    _session_cache = None
    _last_source_id = None
    _last_baseline_stale = False


def last_source_id() -> Optional[str]:
    """The source ID of the most recent successful load_offsets call."""
    return _last_source_id


def is_baseline_stale() -> bool:
    """True if the last load came from the bundled baseline AND the embedded
    build version did not match the running Roblox build version.
    """
    return _last_baseline_stale


# ───────────────────────── parse ─────────────────────────

def _extract_struct_offsets(body: bytes) -> tuple[dict, bytes]:
    """Pull the FFlagList struct member offsets out of the body and return them
    plus the body with the struct region stripped (so flag parsing doesn't
    double-count them).

    Supports both formats:
      A) nested `namespace FFlagList { ... }`
      B) flat siblings inside `namespace FFlagOffsets { uintptr_t FFlagList = ...; }`

    Returns (struct_offsets, body_without_struct_region). struct_offsets keys
    are normalized to the canonical Format A names (Pointer/ToFlag/ToValue).
    """
    struct_offsets: dict[str, int] = {}

    # Format A: nested FFlagList namespace
    m_a = _RX_FFLAGLIST_NAMESPACE.search(body)
    if m_a:
        block = m_a.group(1)
        for nm_b, hx_b in _RX_UINTPTR.findall(block):
            try:
                nm = nm_b.decode("ascii")
            except UnicodeDecodeError:
                continue
            try:
                off = int(hx_b, 16)
            except ValueError:
                continue
            if STRUCT_OFF_MIN <= off <= STRUCT_OFF_MAX:
                struct_offsets.setdefault(nm, off)
        return struct_offsets, _RX_FFLAGLIST_NAMESPACE.sub(b"", body)

    # Format B: siblings inside FFlagOffsets namespace. Pull only the three
    # known struct members so we don't pollute flag_offsets with them.
    m_b = _RX_FFLAGOFFSETS_NAMESPACE.search(body)
    if not m_b:
        return struct_offsets, body
    region = m_b.group(1)
    for nm_b, hx_b in _RX_UINTPTR.findall(region):
        try:
            nm = nm_b.decode("ascii")
        except UnicodeDecodeError:
            continue
        canonical = _STRUCT_ALIASES.get(nm)
        if not canonical:
            continue
        try:
            off = int(hx_b, 16)
        except ValueError:
            continue
        if STRUCT_OFF_MIN <= off <= STRUCT_OFF_MAX:
            struct_offsets.setdefault(canonical, off)
    # Strip only the FFlagOffsets namespace so we don't re-parse its members as flags.
    stripped = _RX_FFLAGOFFSETS_NAMESPACE.sub(b"", body)
    return struct_offsets, stripped


def _parse_imtheo(body: bytes, base_addr: int) -> tuple[dict, dict]:
    """Parse FFlags.hpp body into (flag_offsets, struct_offsets). Format-agnostic."""
    struct_offsets, stripped = _extract_struct_offsets(body)
    flag_offsets: dict = {}

    rejected_range = 0
    for nm_b, hx_b in _RX_UINTPTR.findall(stripped):
        try:
            ident = nm_b.decode("ascii")
        except UnicodeDecodeError:
            continue
        try:
            rva = int(hx_b, 16)
        except ValueError:
            continue
        if rva < RVA_MIN or rva > RVA_MAX:
            rejected_range += 1
            continue
        clean = clean_flag_name(ident)
        if clean in flag_offsets:
            continue
        flag_offsets[clean] = {
            "abs_addr": base_addr + rva,
            "full_name": ident,
            "type": infer_type_from_name(ident) or "unknown",
            "source": "imtheo",
        }

    if rejected_range:
        log(f"[*] Parser: rejected {rejected_range} entries with out-of-range RVA", (180, 180, 180))
    return flag_offsets, struct_offsets


def _parse_imtheo_known_names_only(body: bytes) -> dict[str, str]:
    """Build {stripped_identifier: type} for UI without a process base.
    Format-agnostic (strips struct region for either Format A or B).
    """
    _, stripped = _extract_struct_offsets(body)
    out: dict[str, str] = {}
    for nm_b, hx_b in _RX_UINTPTR.findall(stripped):
        try:
            ident = nm_b.decode("ascii")
        except UnicodeDecodeError:
            continue
        try:
            rva = int(hx_b, 16)
        except ValueError:
            continue
        if rva < RVA_MIN or rva > RVA_MAX:
            continue
        out[ident] = infer_type_from_name(ident) or "unknown"
    return out


def _extract_imtheo_client_version(body: bytes) -> Optional[str]:
    m = _RX_CLIENT_VERSION.search(body)
    if m:
        try:
            return m.group(1).decode("ascii", errors="ignore")
        except Exception:
            return None
    m = _RX_HEADER_VERSION.search(body)
    if m:
        try:
            return m.group(1).decode("ascii", errors="ignore")
        except Exception:
            return None
    return None


def _validate_parsed(flags: dict, structs: dict) -> bool:
    """Reject empty/HTML/captive-portal responses before they poison the cache."""
    return len(flags) >= MIN_VALID_FLAGS and bool(structs.get("Pointer"))


# ───────────────────────── source chain ─────────────────────────

def _try_source(
    source_id: str,
    body: Optional[bytes],
    base_addr: int,
    build_version: str,
) -> Optional[tuple[dict, dict, Optional[str]]]:
    """Parse a fetched body and return (flags, structs, source_build) if valid."""
    if not body:
        return None
    flags, structs = _parse_imtheo(body, base_addr)
    if not _validate_parsed(flags, structs):
        log(f"[!] {source_id}: parsed {len(flags)} flags (need >={MIN_VALID_FLAGS}) - rejected", (255, 200, 100))
        return None
    source_build = _extract_imtheo_client_version(body)
    if build_version and source_build and source_build != build_version:
        log(
            f"[!] VERSION MISMATCH ({source_id}): running '{build_version}' but source is '{source_build}'. "
            f"Memory applies may fail or crash.",
            (255, 120, 120),
        )
    return flags, structs, source_build


def _iter_network_sources():
    """Yield (source_id, fetch_callable) for each (URL, transport) pair.
    Tries requests, then curl, per URL — moving on to the next URL only after
    both transports fail.
    """
    for sid_req, sid_curl, url in PRIMARY_NETWORK_SOURCES:
        yield sid_req, (lambda u=url: offset_sources.fetch_via_requests(u))
        yield sid_curl, (lambda u=url: offset_sources.fetch_via_curl(u))


def _load_chain(base_addr: int, build_version: str) -> tuple[dict, dict, Optional[str], str, bool]:
    """Iterate the source chain. Returns (flags, structs, source_build, source_id, baseline_stale)."""

    for sid, fetch in _iter_network_sources():
        body = fetch()
        if not body:
            continue
        log(f"[+] {sid}: {len(body)} bytes received", (100, 255, 100))
        result = _try_source(sid, body, base_addr, build_version)
        if result is not None:
            flags, structs, source_build = result
            return flags, structs, source_build, sid, False

    # Disk cache fallback
    flags, structs, cache_source_build = _load_from_disk_cache(base_addr, build_version)
    if flags and structs.get("Pointer"):
        return flags, structs, cache_source_build, SRC_DISK_CACHE, False

    # Bundled baseline (last resort)
    body = offset_sources.read_bundled_baseline()
    if body:
        log(f"[+] {SRC_BUNDLED}: {len(body)} bytes read", (100, 255, 100))
        result = _try_source(SRC_BUNDLED, body, base_addr, build_version)
        if result is not None:
            flags, structs, source_build = result
            stale = bool(build_version) and bool(source_build) and source_build != build_version
            if stale:
                log(
                    f"[!] Bundled baseline build '{source_build}' differs from running '{build_version}'. "
                    f"Please update FFM for offsets matching the current Roblox build.",
                    (255, 120, 120),
                )
            return flags, structs, source_build, SRC_BUNDLED, stale

    return {}, {}, None, "", False


def _fetch_body_via_chain() -> tuple[Optional[bytes], str]:
    """Body-only chain for `load_known_flag_names` (no process base required)."""
    for sid, fetch in _iter_network_sources():
        body = fetch()
        if body:
            return body, sid
    body = offset_sources.read_bundled_baseline()
    if body:
        return body, SRC_BUNDLED
    return None, ""


# ───────────────────────── disk cache I/O ─────────────────────────

def _migrate_legacy_cache_if_needed() -> None:
    """One-shot copy of the old in-repo cache to the per-user location."""
    try:
        if os.path.isfile(CACHE_PATH):
            return
        if not os.path.isfile(_LEGACY_CACHE_PATH):
            return
        shutil.copy2(_LEGACY_CACHE_PATH, CACHE_PATH)
        log(f"[*] Migrated legacy cache -> {CACHE_PATH}", (180, 180, 180))
    except Exception as e:
        log(f"[!] Cache migration failed: {type(e).__name__}", (255, 200, 100))


def _write_disk_cache(merged_flags: dict, struct_offsets: dict, build_version: str,
                      base_addr: int, source_build_version: Optional[str] = None) -> None:
    """Persist RVA map for offline warm start. Atomic via tmp + rename."""
    tmp_path = CACHE_PATH + ".tmp"
    try:
        flags_rva = {}
        for clean, info in merged_flags.items():
            rva = info["abs_addr"] - base_addr
            if rva < RVA_MIN or rva > RVA_MAX:
                continue
            flags_rva[clean] = {
                "rva": f"0x{rva:X}",
                "full_name": info["full_name"],
                "type": info["type"],
                "source": info.get("source", "imtheo"),
            }
        cache = {
            "schema_version": 1,
            "source": "imtheo_only",
            "build_version": build_version,
            "source_build_version": source_build_version or "",
            "generated_at": int(time.time()),
            "struct_offsets": {k: f"0x{v:X}" for k, v in struct_offsets.items()},
            "flags": flags_rva,
        }
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp_path, CACHE_PATH)
    except Exception as e:
        log(f"[!] Cache write failed: {type(e).__name__}", (255, 200, 100))
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _load_from_disk_cache(base_addr: int, build_version: str) -> tuple[dict, dict, Optional[str]]:
    """Reconstruct offsets from cache. Returns (flags, structs, source_build)."""
    _migrate_legacy_cache_if_needed()
    if not os.path.isfile(CACHE_PATH):
        return {}, {}, None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"[!] Disk cache read failed: {type(e).__name__}", (255, 200, 100))
        return {}, {}, None

    struct_offsets = {}
    for name, hex_s in data.get("struct_offsets", {}).items():
        if isinstance(hex_s, str) and hex_s.startswith("0x"):
            try:
                struct_offsets[name] = int(hex_s, 16)
            except ValueError:
                pass

    flags = {}
    for clean, info in data.get("flags", {}).items():
        if not isinstance(info, dict):
            continue
        rva_s = info.get("rva", "")
        try:
            rva = int(rva_s, 16) if isinstance(rva_s, str) else int(rva_s)
        except (TypeError, ValueError):
            continue
        if rva < RVA_MIN or rva > RVA_MAX:
            continue
        fn = info.get("full_name", clean)
        flags[clean] = {
            "abs_addr": base_addr + rva,
            "full_name": fn,
            "type": info.get("type", infer_type_from_name(fn) or "unknown"),
            "source": info.get("source", "cache"),
        }

    bv = data.get("build_version", "")
    source_bv = data.get("source_build_version", "")
    if source_bv and build_version and source_bv != build_version:
        log(
            f"[!] CACHE SOURCE VERSION MISMATCH: running '{build_version}' but cached source is '{source_bv}'.",
            (255, 120, 120),
        )
    if bv and build_version and bv != build_version:
        log(
            f"[!] Cache build '{bv}' differs from running '{build_version}' - addresses may be wrong",
            (255, 200, 100),
        )
    elif flags:
        log(f"[+] Loaded {len(flags)} flags from disk cache", (100, 255, 200))
    return flags, struct_offsets, (source_bv or None)


def _known_names_from_disk_cache() -> dict[str, str]:
    """UI preset list when offline: derive from cached flags only."""
    _migrate_legacy_cache_if_needed()
    if not os.path.isfile(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for clean, info in data.get("flags", {}).items():
        if isinstance(info, dict):
            fn = info.get("full_name", clean)
            if isinstance(fn, str):
                out[fn] = info.get("type", infer_type_from_name(fn) or "unknown")
    return out


# ───────────────────────── public API ─────────────────────────

def load_offsets(base_addr: int, build_version: str,
                 user_flag_clean_names: Optional[set[str]] = None) -> tuple[dict, dict]:
    """Load offsets via the 6-source fallback chain.

    user_flag_clean_names is ignored (kept for API compatibility); always loads
    full source map.
    """
    global _session_cache, _last_source_id, _last_baseline_stale
    if _session_cache is not None and _session_cache.get("base_addr") == base_addr:
        return _session_cache["flags"], _session_cache["structs"]

    flags, structs, source_build, source_id, baseline_stale = _load_chain(base_addr, build_version)

    _last_source_id = source_id or None
    _last_baseline_stale = baseline_stale

    if flags or structs:
        log(
            f"[OK] Offsets source: {source_id or 'none'}, "
            f"build={source_build or 'unknown'}, flags={len(flags)}",
            (100, 255, 200),
        )
    else:
        log("[!] All offset sources failed - FFM cannot apply flags", (255, 100, 100))

    _session_cache = {"base_addr": base_addr, "flags": flags, "structs": structs}

    # Only persist when we got a fresh body. Don't echo cache back to itself
    # and don't persist the bundled baseline as if it were a fresh fetch.
    if (flags or structs) and source_id not in (SRC_DISK_CACHE, SRC_BUNDLED, ""):
        _write_disk_cache(flags, structs, build_version, base_addr, source_build_version=source_build)

    return flags, structs


def load_known_flag_names() -> dict[str, str]:
    """Known-flag list for UI: name+type only, no process base required.

    Uses the same fallback chain as load_offsets for body fetch, then disk
    cache as a final fallback for the preset list.
    """
    body, source_id = _fetch_body_via_chain()
    if body:
        names = _parse_imtheo_known_names_only(body)
        if len(names) >= MIN_VALID_FLAGS:
            log(f"[+] Known names from {source_id}: {len(names)}", (100, 255, 100))
            return names
        else:
            log(f"[!] {source_id}: only {len(names)} names - falling back", (255, 200, 100))

    fallback = _known_names_from_disk_cache()
    if fallback:
        log(f"[+] Known names from disk cache: {len(fallback)}", (255, 200, 100))
        return fallback

    log("[!] No offset source returned a usable name list - UI search limited", (255, 100, 100))
    return {}
