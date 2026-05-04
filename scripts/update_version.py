"""
Syncs the version string into version.json, logo.svg, and README.md.

Usage:
  python scripts/update_version.py            # uses version.json as source of truth
  python scripts/update_version.py 3.3.6      # explicit version, writes version.json too
  python scripts/update_version.py v3.3.6     # leading 'v' is accepted

The GitHub Actions release workflow calls this with the tag-derived version.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.json"
SVG_FILE = ROOT / "logo.svg"
README_FILE = ROOT / "README.md"


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
    readme_changed = patch_readme(version)

    print(f"  logo.svg  -> {'updated' if svg_changed else 'no change'}")
    print(f"  README.md -> {'updated' if readme_changed else 'no change'}")


if __name__ == "__main__":
    main()
