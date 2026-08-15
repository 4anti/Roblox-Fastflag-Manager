"""Decision logic for the 'Fix Roblox' flow (Phase 1: offsets-first)."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional


def decide_fix_action(installed: Optional[str],
                      upstream_build: Optional[str]) -> str:
    """Decide what Fix Roblox should do, given the installed Roblox build GUID
    and the build the upstream dump currently targets.

    Returns one of:
      "no_roblox"      - no Roblox install detected
      "refresh_failed" - the network probe returned no usable build
      "resolved"       - upstream now matches installed; reload offsets, done
      "needs_download" - builds differ; requires the Phase 2 downloader
    """
    if not installed or installed == "unknown":
        return "no_roblox"
    if not upstream_build:
        return "refresh_failed"
    if upstream_build == installed:
        return "resolved"
    return "needs_download"


def is_version_mismatch(installed: Optional[str],
                        offsets_target: Optional[str]) -> bool:
    """True only when BOTH the installed Roblox build and the build the loaded
    offsets target are known AND they differ.

    This is the honest signal behind the version indicator: injection resolves
    addresses for `offsets_target`, so if that isn't the build actually
    installed, applies may fail or crash. Unknown/missing data on either side is
    treated as "no mismatch" so a transient gap (e.g. offsets still loading, no
    Roblox detected) never raises a false alarm.
    """
    if not installed or installed == "unknown":
        return False
    if not offsets_target:
        return False
    return installed != offsets_target


def classify_version_card(installed: Optional[str],
                          offsets_target: Optional[str],
                          latest_production: Optional[str]) -> str:
    """Pick the Advanced-tab card state from three independent build strings.

    installed          - disk-newest Roblox folder, or None / 'unknown'
    offsets_target     - build the loaded offset dump was taken from
    latest_production  - Roblox CDN LATEST, or None if unreachable

    A download can only help when this install is behind CDN latest.
    When the install already IS latest and the dump has not caught up,
    Refresh must not pretend a Roblox download 'fixed' anything.
    """
    if not installed or installed == "unknown":
        return "no_roblox"
    if not offsets_target:
        return "offsets_pending"
    aligned = installed == offsets_target
    if latest_production:
        if installed == latest_production:
            return "aligned" if aligned else "offsets_lagging"
        return "aligned_update_available" if aligned else "needs_roblox_update"
    return "aligned" if aligned else "mismatch_offline"


def plan_upgrade(installed: Optional[str], target: Optional[str],
                 is_older) -> dict:
    """Decide whether to upgrade, refuse a downgrade, no-op, or bail.

    is_older(target, installed) -> bool tells us whether `target` is an OLDER
    build than `installed` (injected so this stays pure/testable).

    Returns {'action': <str>, 'target': <str|None>}:
      "no_target"          - target build unknown
      "already_matching"   - installed already equals target
      "blocked_downgrade"  - target is older than installed (upgrade-only policy)
      "upgrade"            - proceed to download/install `target`
    """
    if not target:
        return {"action": "no_target", "target": None}
    if installed == target:
        return {"action": "already_matching", "target": target}
    if installed and is_older(target, installed):
        return {"action": "blocked_downgrade", "target": target}
    return {"action": "upgrade", "target": target}


def select_packages_to_fetch(packages, find_cached, cache_dirs) -> list:
    """Return the subset of packages NOT already present (correct checksum) in
    cache_dirs. `find_cached(package, cache_dirs)` returns a path or None
    (injected so this stays pure/testable)."""
    return [p for p in packages if not find_cached(p, cache_dirs)]


def _get_manifest_packages(target_guid: str) -> Optional[list]:
    """Fetch + parse the package manifest, trying each CDN base in turn.
    Returns the package list, or None if no CDN yields a parseable manifest."""
    from src.core.version_changer import manifest, downloader
    for base in downloader.CDN_BASES:
        text = manifest.fetch_manifest_text(base, target_guid)
        if not text:
            continue
        try:
            packages = manifest.parse_manifest(text)
        except ValueError:
            continue
        if packages:
            return packages
    return None


def _format_mb(n: float) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def _is_complete_player_build(build_dir: str) -> bool:
    """True when a version folder has a non-empty player exe.

    A leftover `version-{guid}/` from a crashed or cancelled install is not a
    finished build. Treating it as already-present would skip the real
    download forever.
    """
    if not build_dir or not os.path.isdir(build_dir):
        return False
    for name in ("RobloxPlayerBeta.exe", "RobloxPlayer.exe"):
        exe = os.path.join(build_dir, name)
        try:
            if os.path.isfile(exe) and os.path.getsize(exe) > 0:
                return True
        except OSError:
            continue
    return False


def _remove_leftover_build(path: str) -> Optional[str]:
    """Delete an incomplete leftover at `path`. Returns an error string on
    failure, else None."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        return None
    except OSError as e:
        return str(e)


def _production_folder_name(production_guid: str) -> str:
    g = (production_guid or "").strip()
    if not g:
        return ""
    return g if g.startswith("version-") else f"version-{g}"


def prune_non_production_builds(versions_root: str, production_guid: str) -> dict:
    """Delete version-* folders in `versions_root` that are not production latest.

    Only runs when the production folder already has a player exe, so a failed
    delete can never leave the user with zero Roblox. Leftover other-channel or
    previous-production folders are what keep Launch/Play on the wrong client.
    """
    from src.utils.logger import log
    empty = {"removed": [], "failed": [], "kept": None}
    keep_name = _production_folder_name(production_guid)
    if not versions_root or not keep_name or not os.path.isdir(versions_root):
        return empty
    keep_path = os.path.join(versions_root, keep_name)
    if not _is_complete_player_build(keep_path):
        return empty
    removed = []
    failed = []
    try:
        names = os.listdir(versions_root)
    except OSError:
        return {"removed": [], "failed": [], "kept": keep_path}
    for name in names:
        if name == keep_name or not name.startswith("version-"):
            continue
        path = os.path.join(versions_root, name)
        if not os.path.isdir(path):
            continue
        err = _remove_leftover_build(path)
        if err:
            failed.append(name)
            log(f"[!] Could not remove leftover {name}: {err}", (255, 200, 100))
        else:
            removed.append(name)
            log(f"[*] Removed leftover Roblox build {name}", (150, 200, 255))
    # A leftover launcher sitting next to the version folders can still start
    # an old client after we launch the production player exe.
    for extra in ("RobloxPlayerLauncher.exe", "RobloxPlayerInstaller.exe"):
        extra_path = os.path.join(versions_root, extra)
        if not os.path.isfile(extra_path):
            continue
        err = _remove_leftover_build(extra_path)
        if err:
            failed.append(extra)
            log(f"[!] Could not remove leftover {extra}: {err}", (255, 200, 100))
        else:
            removed.append(extra)
            log(f"[*] Removed leftover {extra}", (150, 200, 255))
    return {"removed": removed, "failed": failed, "kept": keep_path}


def prune_stock_non_production(production_guid: str) -> dict:
    """Prune leftover version folders in the stock Roblox Versions tree only.

    Third-party launcher trees are left alone. Best-effort: never raises.
    """
    empty = {"removed": [], "failed": [], "kept": None}
    try:
        from src.core.roblox_manager import RobloxManager
        stock = RobloxManager.get_stock_versions_root()
        if not stock or not os.path.isdir(stock):
            return empty
        return prune_non_production_builds(stock, production_guid)
    except Exception:
        return empty


def disk_space_precheck(packages, versions_root, cache_dirs):
    """Return a human-readable error if there isn't enough free disk space to
    download + install the build, else None.

    Best-effort: returns None (allow) if anything can't be measured, so a flaky
    measurement never blocks a legitimate upgrade.
    """
    from src.core.version_changer import downloader
    try:
        total_uncompressed = sum(int(p.get("size", 0)) for p in packages)
        to_download = sum(
            int(p.get("packed_size", 0))
            for p in packages
            if not downloader.find_cached(p, cache_dirs)
        )
        # Peak staging usage: the zips we still need to fetch + the fully
        # extracted build. Final footprint on the install drive: the build.
        need_temp = to_download + total_uncompressed
        need_final = total_uncompressed
        margin = 1.15  # headroom for filesystem overhead / temp files

        temp_dir = tempfile.gettempdir()
        if shutil.disk_usage(temp_dir).free < need_temp * margin:
            return (
                f"Not enough free disk space to download the update — about "
                f"{_format_mb(need_temp * margin)} is needed on the drive holding "
                f"temporary files. Free up some space and try again."
            )
        target = versions_root if os.path.exists(versions_root) else os.path.dirname(versions_root)
        if target and shutil.disk_usage(target).free < need_final * margin:
            return (
                f"Not enough free disk space to install Roblox — about "
                f"{_format_mb(need_final * margin)} is needed where Roblox lives. "
                f"Free up some space and try again."
            )
    except Exception:
        return None
    return None


def run_upgrade(target_guid: str, versions_root: str, cache_dirs: list,
                progress=None, should_cancel=None) -> dict:
    """Download + install the build `target_guid` into versions_root/version-{guid}.

    Reuses the tested engine units. Downloads to a temp staging dir, extracts each
    package into a build root, writes AppSettings.xml, and atomically commits only
    after every package is in place. A crash/cancel before commit discards the
    staging dir, leaving the real install untouched.

    progress(done_packages, total_packages, package_name) — optional callback.
    should_cancel() -> bool — optional; checked before each package.

    Returns {'ok': bool, 'state': str, 'final_path': str|None, 'message': str}.
    Success states (ok=True): 'installed' (fresh install completed),
    'already_present' (target folder already has a player exe; nothing downloaded).
    Failure states (ok=False): 'manifest_failed', 'download_failed',
    'cancelled', 'insufficient_space', 'error'.
    """
    from src.core.version_changer import downloader, installer
    from src.utils.logger import log

    # Short-circuit: if the target build is already sitting on disk, don't
    # waste bandwidth re-fetching it. Callers should treat this as success
    # because the desired end-state (target build present) is already met.
    final_name = target_guid if target_guid.startswith("version-") else f"version-{target_guid}"
    early_path = os.path.join(versions_root, final_name)
    if os.path.exists(early_path):
        if _is_complete_player_build(early_path):
            log(f"[*] {final_name} is already installed at {early_path}", (150, 200, 255))
            return {"ok": True, "state": "already_present", "final_path": early_path,
                    "message": "That build is already installed."}
        log(f"[!] {final_name} exists but has no player exe; removing leftover "
            f"and continuing the download", (255, 200, 100))
        err = _remove_leftover_build(early_path)
        if err:
            return {"ok": False, "state": "error", "final_path": None,
                    "message": f"Could not replace leftover {final_name}: {err}"}
    packages = _get_manifest_packages(target_guid)
    if not packages:
        return {"ok": False, "state": "manifest_failed", "final_path": None,
                "message": "Could not fetch the Roblox package manifest."}

    # Fail early with a clear message rather than dying mid-download.
    space_err = disk_space_precheck(packages, versions_root, cache_dirs)
    if space_err:
        return {"ok": False, "state": "insufficient_space", "final_path": None,
                "message": space_err}

    staging = tempfile.mkdtemp(prefix="ffm_rbx_")
    build_root = os.path.join(staging, "build")
    os.makedirs(build_root, exist_ok=True)
    total = len(packages)
    try:
        for i, pkg in enumerate(packages):
            if should_cancel and should_cancel():
                return {"ok": False, "state": "cancelled", "final_path": None,
                        "message": "Download cancelled."}
            src = downloader.find_cached(pkg, cache_dirs)
            if not src:
                src = downloader.download_package(pkg, target_guid, staging)
            if not src:
                return {"ok": False, "state": "download_failed", "final_path": None,
                        "message": f"Failed to download {pkg['name']}."}
            try:
                installer.extract_package(src, pkg["name"], build_root)
            except ValueError:
                # Unknown package in the manifest: skip rather than abort, so a
                # newly-added Roblox package doesn't break the whole install.
                log(f"[!] Skipping unmapped package {pkg['name']}", (255, 200, 100))
            if progress:
                progress(i + 1, total, pkg["name"])

        installer.write_appsettings(build_root)
        try:
            final_path = installer.commit_build(build_root, versions_root, target_guid)
        except FileExistsError:
            # Another process (or an earlier FFM run) landed the same build
            # while we were downloading. Only treat that as success when the
            # dest actually has a player exe; an empty leftover is replaced.
            existing = os.path.join(versions_root, final_name)
            if _is_complete_player_build(existing):
                log(f"[*] {final_name} was installed concurrently at {existing}",
                    (150, 200, 255))
                return {"ok": True, "state": "already_present", "final_path": existing,
                        "message": "That build is already installed."}
            log(f"[!] {final_name} exists but has no player exe; replacing leftover",
                (255, 200, 100))
            err = _remove_leftover_build(existing)
            if err:
                return {"ok": False, "state": "error", "final_path": None,
                        "message": f"Could not replace leftover {final_name}: {err}"}
            try:
                final_path = installer.commit_build(build_root, versions_root, target_guid)
            except Exception as e:
                return {"ok": False, "state": "error", "final_path": None,
                        "message": f"Could not install {final_name}: {e}"}
        return {"ok": True, "state": "installed", "final_path": final_path,
                "message": "Roblox build installed."}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
