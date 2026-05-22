"""
Syncs the version string into version.json, logo.svg, and README.md, and
refreshes the logo's "NNK+ FastFlags Available!" count from data/FFlags.hpp.

Usage:
  python scripts/update_version.py            # uses version.json as source of truth
  python scripts/update_version.py 3.3.6      # explicit version, writes version.json too
  python scripts/update_version.py v3.3.6     # leading 'v' is accepted

The GitHub Actions release workflow calls this with the tag-derived version.
"""

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.json"
SVG_FILE = ROOT / "logo.svg"
README_FILE = ROOT / "README.md"
MIRROR_FFLAGS = ROOT / "data" / "FFlags.hpp"
BUNDLED_BASELINE = ROOT / "src" / "data" / "FFlags_baseline.hpp"


def read_version() -> str:
    with open(VERSION_FILE, encoding="utf-8") as f:
        return json.load(f)["version"]


def write_version(version: str) -> None:
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": version}, f, indent=4)
        f.write("\n")


def patch_svg(version: str) -> bool:
    text = SVG_FILE.read_text(encoding="utf-8")
    updated = re.sub(r"FFM — v[\d.]+", f"FFM — v{version}", text)
    if updated == text:
        return False
    SVG_FILE.write_text(updated, encoding="utf-8")
    return True


def count_flag_offsets() -> int:
    """Count modifiable FFlag offsets in the mirrored FFlags.hpp.

    Mirrors the badge logic in .github/workflows/mirror-offsets.yml: total
    `uintptr_t NAME = ...` entries minus the FFlagList struct members (Format A
    nested block). Returns 0 if the file is missing/unreadable.
    """
    try:
        text = MIRROR_FFLAGS.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    total = len(re.findall(r"uintptr_t\s+\w+\s*=", text))
    m = re.search(r"namespace FFlagList\s*\{(.*?)\}", text, re.S)
    struct = len(re.findall(r"uintptr_t\s+\w+\s*=", m.group(1))) if m else 0
    return max(total - struct, 0)


def flag_count_label(n: int) -> str:
    """Format an offset count like the badge: '12K+' for >=1000, else the number."""
    return f"{n // 1000}K+" if n >= 1000 else str(n)


def patch_svg_flag_count() -> bool:
    """Patch logo.svg's 'NNK+ FastFlags Available!' text from the live mirror count.

    Skips patching when the mirror looks truncated (<500 offsets) so an upstream
    dumper outage can't stamp a bogus '0'/'3' into the logo.
    """
    n = count_flag_offsets()
    if n < 500:
        print(f"[!] patch_svg_flag_count: mirror has only {n} offsets "
              f"(<500) — leaving logo flag count unchanged.")
        return False
    label = flag_count_label(n)
    text = SVG_FILE.read_text(encoding="utf-8")
    updated = re.sub(r"[\d]+[KkMm]?\+? FastFlags Available!",
                     f"{label} FastFlags Available!", text)
    if updated == text:
        return False
    SVG_FILE.write_text(updated, encoding="utf-8")
    print(f"    (logo flag count -> {label}, from {n} offsets)")
    return True


def refresh_baseline() -> bool:
    """Copy the freshest mirrored FFlags.hpp into the bundled baseline so
    each release ships with up-to-date offsets even if a user has no network.
    Returns True if the baseline file was changed.
    """
    if not MIRROR_FFLAGS.is_file():
        return False
    # Guard against shipping a truncated/stub mirror (e.g. a broken dumper that
    # returned only the 3 FFlagList struct offsets mid-Roblox-update). Refuse to
    # overwrite the bundled baseline with a near-empty file.
    try:
        offset_count = MIRROR_FFLAGS.read_text(errors="ignore").count("uintptr_t")
    except OSError:
        offset_count = 0
    if offset_count < 500:
        print(f"[!] refresh_baseline: mirror has only {offset_count} offsets "
              f"(<500) — refusing to overwrite the bundled baseline.")
        return False
    BUNDLED_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    if BUNDLED_BASELINE.is_file():
        try:
            if BUNDLED_BASELINE.read_bytes() == MIRROR_FFLAGS.read_bytes():
                return False
        except OSError:
            pass
    shutil.copy2(MIRROR_FFLAGS, BUNDLED_BASELINE)
    return True


def patch_readme(version: str) -> bool:
    text = README_FILE.read_text(encoding="utf-8")
    updated = re.sub(
        r"(!\[Version\]\(https://img\.shields\.io/badge/version-)v[\d.]+-",
        rf"\1v{version}-",
        text,
    )
    if updated == text:
        return False
    README_FILE.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    if len(sys.argv) > 1:
        version = sys.argv[1].lstrip("v")
        write_version(version)
        print(f"version.json -> v{version}")
    else:
        version = read_version()
        print(f"Reading from version.json: v{version}")

    svg_changed = patch_svg(version)
    svg_count_changed = patch_svg_flag_count()
    readme_changed = patch_readme(version)
    baseline_changed = refresh_baseline()

    print(f"  logo.svg (version)       -> {'updated' if svg_changed else 'no change'}")
    print(f"  logo.svg (flag count)    -> {'updated' if svg_count_changed else 'no change'}")
    print(f"  README.md                -> {'updated' if readme_changed else 'no change'}")
    print(f"  src/data/FFlags_baseline -> {'updated' if baseline_changed else 'no change'}")


if __name__ == "__main__":
    main()
