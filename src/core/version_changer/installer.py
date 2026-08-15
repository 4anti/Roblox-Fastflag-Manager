"""Assemble a downloaded Roblox build: extract packages into their mapped
subfolders, write AppSettings.xml, and atomically commit into the Versions root.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from typing import Optional

from src.utils.logger import log

# Public Bloxstrap/Fishstrap package -> install subfolder map (player).
PACKAGE_DIRECTORY_MAP = {
    "RobloxApp.zip": "",
    "Libraries.zip": "",
    "redist.zip": "",
    "shaders.zip": "shaders/",
    "ssl.zip": "ssl/",
    "WebView2.zip": "",
    "WebView2RuntimeInstaller.zip": "WebView2RuntimeInstaller/",
    "content-avatar.zip": "content/avatar/",
    "content-configs.zip": "content/configs/",
    "content-fonts.zip": "content/fonts/",
    "content-sky.zip": "content/sky/",
    "content-sounds.zip": "content/sounds/",
    "content-textures2.zip": "content/textures/",
    "content-models.zip": "content/models/",
    "content-textures3.zip": "PlatformContent/pc/textures/",
    "content-terrain.zip": "PlatformContent/pc/terrain/",
    "content-platform-fonts.zip": "PlatformContent/pc/fonts/",
    "content-platform-dictionaries.zip": "PlatformContent/pc/shared_compression_dictionaries/",
    "extracontent-luapackages.zip": "ExtraContent/LuaPackages/",
    "extracontent-translations.zip": "ExtraContent/translations/",
    "extracontent-models.zip": "ExtraContent/models/",
    "extracontent-textures.zip": "ExtraContent/textures/",
    "extracontent-places.zip": "ExtraContent/places/",
}

_APPSETTINGS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<Settings>\n"
    "\t<ContentFolder>content</ContentFolder>\n"
    "\t<BaseUrl>http://www.roblox.com</BaseUrl>\n"
    "</Settings>\n"
)


def target_subdir(package_name: str) -> Optional[str]:
    """Return the install subfolder for a package, or None if unknown."""
    return PACKAGE_DIRECTORY_MAP.get(package_name)


def extract_package(zip_path: str, package_name: str, dest_root: str) -> None:
    """Extract a package zip into its mapped subfolder under dest_root.

    Raises ValueError for an unmapped package (callers must not extract blindly).
    """
    sub = target_subdir(package_name)
    if sub is None:
        raise ValueError(f"unmapped package: {package_name}")
    out_dir = os.path.join(dest_root, *sub.split("/")) if sub else dest_root
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)


def write_appsettings(dest_root: str) -> None:
    """Write the standard AppSettings.xml into a build root."""
    with open(os.path.join(dest_root, "AppSettings.xml"), "w", encoding="utf-8") as f:
        f.write(_APPSETTINGS)


def commit_build(staging_root: str, versions_root: str, guid: str) -> str:
    """Atomically move a fully-staged build into versions_root/version-{guid}.

    The guid argument may be the bare guid or the full 'version-...' string; the
    final folder name is always the full 'version-...'. Returns the final path.
    Raises if the destination already exists (caller decides on re-install).
    """
    final_name = guid if guid.startswith("version-") else f"version-{guid}"
    os.makedirs(versions_root, exist_ok=True)
    final_path = os.path.join(versions_root, final_name)
    if os.path.exists(final_path):
        raise FileExistsError(final_path)
    shutil.move(staging_root, final_path)
    log(f"[+] Installed build at {final_path}", (100, 255, 100))
    return final_path
