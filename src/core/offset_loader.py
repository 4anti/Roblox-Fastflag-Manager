"""Centralized FFlag offset loader — ImTheo-hosted only.

All flag RVAs and FFlagList struct offsets come from
https://imtheo.lol/Offsets/FFlags.hpp . No local FFlags.json or dump_metadata.

Offline: last-good offsets_cache.json is loaded when the fetch fails.

Defensive parsing: treat remote body as untrusted (size cap, regex, RVA ranges,
ASCII names). Writes still go through `write_flag_at_address` region checks.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from src.utils.logger import log
from src.utils.helpers import clean_flag_name, infer_type_from_name


# ───────────────────────── paths ─────────────────────────

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMTHEO_FFLAGS_HPP = "https://imtheo.lol/Offsets/FFlags.hpp"
CACHE_PATH = os.path.join(_REPO_ROOT, "offsets_cache.json")


# ───────────────────────── safety constants ─────────────────────────

IMTHEO_MAX_BYTES = 5 * 1024 * 1024

RVA_MIN = 0x100000
RVA_MAX = 0x10000000

STRUCT_OFF_MIN = 0x100000
STRUCT_OFF_MAX = 0x10000000

_RX_UINTPTR = re.compile(
    rb'inline\s+constexpr\s+uintptr_t\s+([A-Za-z_][A-Za-z0-9_]{0,127})\s*=\s*0x([0-9a-fA-F]{1,8})'
)
_RX_FFLAGLIST = re.compile(rb'namespace\s+FFlagList\s*\{([\s\S]{0,65536}?)\}', re.MULTILINE)
_RX_CLIENT_VERSION = re.compile(rb'ClientVersion\s*=\s*"([^"]+)"')
_RX_HEADER_VERSION = re.compile(rb'Roblox Version\s*:\s*(version-[0-9a-fA-F]+)')


# ───────────────────────── module cache ─────────────────────────

_session_cache: Optional[dict] = None


def reset_cache():
    global _session_cache
    _session_cache = None


# ───────────────────────── imtheo fetch + parse ─────────────────────────

def _fetch_imtheo_body(url: str) -> Optional[bytes]:
    """HTTPS fetch with size cap. Returns bytes or None on failure."""
    if not url.startswith("https://"):
        log(f"[!] Imtheo: refusing non-HTTPS URL {url}", (255, 100, 100))
        return None
    try:
        import requests
    except ImportError:
        log("[-] Imtheo: requests module unavailable", (255, 200, 100))
        return None

    try:
        resp = requests.get(url, timeout=10, stream=True)
    except Exception as e:
        log(f"[!] Imtheo fetch failed: {e}", (255, 200, 100))
        return None

    if resp.status_code != 200:
        log(f"[!] Imtheo HTTP {resp.status_code}", (255, 200, 100))
        resp.close()
        return None

    body = bytearray()
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > IMTHEO_MAX_BYTES:
            log(f"[!] Imtheo: response exceeded {IMTHEO_MAX_BYTES} byte cap — rejecting", (255, 100, 100))
            resp.close()
            return None
    resp.close()
    return bytes(body)


def _parse_imtheo(body: bytes, base_addr: int) -> tuple[dict, dict]:
    """Parse ImTheo FFlags.hpp into (flag_offsets, struct_offsets)."""
    flag_offsets = {}
    struct_offsets = {}

    m = _RX_FFLAGLIST.search(body)
    if m:
        block = m.group(1)
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

    stripped = _RX_FFLAGLIST.sub(b"", body)

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
        log(f"[*] Imtheo: rejected {rejected_range} entries with out-of-range RVA", (180, 180, 180))
    return flag_offsets, struct_offsets


def _parse_imtheo_known_names_only(body: bytes) -> dict[str, str]:
    """Build {stripped_identifier: type} for UI without a process base."""
    stripped = _RX_FFLAGLIST.sub(b"", body)
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
    """Extract Roblox build version embedded in Imtheo payload."""
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


def _load_imtheo(base_addr: int, build_version: str) -> tuple[dict, dict, Optional[str]]:
    """Fetch + parse ImTheo FFlags.hpp. Returns ({}, {}) on failure."""
    body = _fetch_imtheo_body(IMTHEO_FFLAGS_HPP)
    if not body:
        return {}, {}, None
    log(f"[+] Imtheo: {len(body)} bytes received", (100, 255, 100))
    imtheo_build = _extract_imtheo_client_version(body)
    if imtheo_build:
        log(f"[*] Imtheo build: {imtheo_build}", (150, 200, 255))
        if build_version and imtheo_build != build_version:
            log(
                f"[!] VERSION MISMATCH: running '{build_version}' but Imtheo is '{imtheo_build}'. "
                f"Memory applies may fail or crash.",
                (255, 120, 120),
            )
    flag_offsets, struct_offsets = _parse_imtheo(body, base_addr)
    if struct_offsets.get("Pointer"):
        log(f"[+] Imtheo: FFlagList Pointer=0x{struct_offsets['Pointer']:X}", (100, 255, 100))
    log(f"[+] Imtheo: {len(flag_offsets)} flag offsets parsed", (100, 255, 100))
    return flag_offsets, struct_offsets, imtheo_build


def _write_disk_cache(merged_flags: dict, struct_offsets: dict, build_version: str,
                      base_addr: int, source_build_version: Optional[str] = None) -> None:
    """Persist RVA map for offline warm start."""
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
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        log(f"[!] Cache write failed: {e}", (255, 200, 100))


def _load_from_disk_cache(base_addr: int, build_version: str) -> tuple[dict, dict]:
    """Reconstruct offsets from offsets_cache.json (ImTheo-derived cache only)."""
    if not os.path.isfile(CACHE_PATH):
        return {}, {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"[!] Disk cache read failed: {e}", (255, 200, 100))
        return {}, {}

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
            f"[!] CACHE SOURCE VERSION MISMATCH: running '{build_version}' but cached source is '{source_bv}'. "
            f"Memory applies may fail or crash.",
            (255, 120, 120),
        )
    if bv and build_version and bv != build_version:
        log(
            f"[!] Cache build '{bv}' differs from running '{build_version}' — addresses may be wrong",
            (255, 200, 100),
        )
    elif flags:
        log(f"[+] Loaded {len(flags)} flags from disk cache", (100, 255, 200))
    return flags, struct_offsets


def _known_names_from_disk_cache() -> dict[str, str]:
    """UI preset list when offline: derive from cached flags only."""
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
    """Load offsets from ImTheo only. Disk cache fallback if fetch fails.

    user_flag_clean_names is ignored (kept for API compatibility); always loads full Imtheo map.
    """
    global _session_cache
    if _session_cache is not None and _session_cache.get("base_addr") == base_addr:
        return _session_cache["flags"], _session_cache["structs"]

    flags, structs, source_build = _load_imtheo(base_addr, build_version)

    if not flags and not structs:
        log("[*] Imtheo unavailable — trying disk cache", (255, 200, 100))
        flags, structs = _load_from_disk_cache(base_addr, build_version)
        source_build = None

    _session_cache = {"base_addr": base_addr, "flags": flags, "structs": structs}
    if flags or structs:
        _write_disk_cache(flags, structs, build_version, base_addr, source_build_version=source_build)
    return flags, structs


def load_known_flag_names() -> dict[str, str]:
    """Known-flag list for UI: Imtheo FFlags.hpp names only (no local JSON).

    Works without Roblox; uses fetch, then disk cache of prior Imtheo snapshot.
    """
    body = _fetch_imtheo_body(IMTHEO_FFLAGS_HPP)
    if body:
        names = _parse_imtheo_known_names_only(body)
        if names:
            log(f"[+] Imtheo known names: {len(names)} (UI search)", (100, 255, 100))
            return names

    fallback = _known_names_from_disk_cache()
    if fallback:
        log(f"[+] Known names from disk cache: {len(fallback)}", (255, 200, 100))
        return fallback

    log("[!] No Imtheo names and no cache — UI search limited", (255, 100, 100))
    return {}
