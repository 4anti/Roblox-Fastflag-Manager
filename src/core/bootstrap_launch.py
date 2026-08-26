"""
bootstrap_launch.py - lightweight join/launch logic shared by the standalone
bootstrapper stub (bootstrap.pyw) and FFM's own protocol handoff (main.pyw).

Deliberately GUI-free and import-light so it can run in a headless bootstrapper
process (Fishstrap / Bloxstrap style) without spinning up the webview app.

Start-only flags (ClientAppSettings.json read at RobloxPlayerBeta boot) must
be on disk in the launched folder before CreateProcess. A failed write is
retried once; a second miss does not launch.
"""

from src.utils.logger import log


def write_startup_flags(guid=None):
    """Write enabled flags to ClientAppSettings.json, flush, and read back
    the folder we are about to launch. STOCK versions only (third-party
    bootstrapper installs are skipped so we don't overwrite their settings).

    Returns True only when the launched guid's JSON contains every enabled
    flag (or there are no enabled flags). Failures do not launch.
    """
    try:
        from src.core.flag_manager import FlagManager
        from src.core.roblox_manager import RobloxManager

        RobloxManager.mark_startup_write()
        fm = FlagManager()
        fm.load_user_flags()
        flags_dict = fm.build_clientapp_dict()
        if not flags_dict and not fm.user_flags:
            log("[*] Bootstrapper: no flags to write", (100, 255, 255))
            return True
        ok, msg = RobloxManager.apply_fflags_json(flags_dict, prefer_guid=guid)
        if not ok:
            log(f"[!] Bootstrapper flag write skipped: {msg}", (255, 200, 100))
            return False

        vdir = RobloxManager.version_dir_for_guid(guid)
        if not vdir:
            vdir = RobloxManager.get_roblox_version_dir()
        if flags_dict and not RobloxManager.clientapp_matches(vdir, flags_dict):
            log(f"[!] Startup flags missing in {vdir}",
                (255, 200, 100))
            return False

        log("[*] Bootstrapper: wrote startup flags to ClientAppSettings.json",
            (100, 255, 255))
        return True
    except Exception as e:
        log(f"[!] Bootstrapper flag write skipped: {e}", (255, 200, 100))
        return False


def _ensure_startup_flags(guid):
    """Write+verify, retry once. False means the caller must not launch."""
    if write_startup_flags(guid):
        return True
    log("[!] Startup flags missing, retrying write...", (255, 200, 100))
    if write_startup_flags(guid):
        return True
    log(f"[!] Startup flags missing in {guid} - not launching", (255, 120, 120))
    return False


def _restore_third_party_if_transient():
    """If FFM only seized the launcher transiently to fix a third-party
    bootstrapper's version (Take-over-only-for-the-fix), hand the handler back now
    that the fix+launch is done, so the NEXT click routes through the bootstrapper
    again (with its mods). Best-effort — a failure never blocks the join."""
    try:
        from src.utils.config import Config
        from src.core.version_changer import bootstrapper
        s = Config.load_settings()
        backup = s.get('_rbx_handler_backup')
        primary = (backup.get(bootstrapper._PRIMARY_SCHEME)
                   if isinstance(backup, dict) else backup)
        # Only restore when the backed-up handler is a THIRD-PARTY bootstrapper —
        # a stock/none backup means the user opted FFM in as the persistent handler.
        if primary and bootstrapper.classify_handler(primary) == "third_party":
            bootstrapper.restore(backup)
            s['_rbx_handler_backup'] = None
            s['roblox_fix_mode'] = 'launch_only'
            Config.save_settings(s)
            log("[*] Handed the launcher back to the original bootstrapper "
                "(transient version-fix done)", (100, 255, 255))
    except Exception as e:
        log(f"[!] Transient handler restore skipped: {e}", (255, 200, 100))


def _prune_stock_if_closed(production_guid):
    """Remove leftover stock version folders when no Roblox process is live."""
    if not production_guid:
        return
    try:
        from src.core.roblox_manager import RobloxManager
        if RobloxManager.is_roblox_running():
            return
        from src.core.version_changer import fixer
        fixer.prune_stock_non_production(production_guid)
    except Exception:
        pass


def launch_join(uri):
    """
    Version-fix (only on a fixable mismatch) then launch the matching Roblox build
    with the original join URI. One shared implementation for both entry points.

    Whatever happens, if FFM only holds the launcher transiently (seized from a
    third-party bootstrapper just to fix the version), the handler is handed back
    afterwards so the bootstrapper's mods keep working on later launches.
    """
    from src.core.roblox_manager import RobloxManager
    from src.core.version_changer import fixer, deployment, fastpath

    try:
        RobloxManager.mark_startup_write()
    except Exception:
        pass
    try:
        rm = RobloxManager()
        installed = RobloxManager.get_roblox_version_string()
        target = installed

        # Fast path: build already confirmed latest -> write flags, launch, no network.
        if fastpath.is_up_to_date(installed):
            try:
                log("[*] Fast join: build already up to date, launching...", (100, 255, 255))
                _prune_stock_if_closed(installed)
                if _ensure_startup_flags(installed):
                    ok, _ = rm.launch_specific_version(installed, args=uri)
                    if ok:
                        return
            except Exception as e:
                log(f"[!] Fast join failed, falling back: {e}", (255, 200, 100))

        try:
            # Sync to Roblox's LATEST production build (never downgrade). Live
            # memory injection may still miss when the offset dump lags this
            # build; the apply-flow guard skips memory writes in that window,
            # falling back to JSON-only. See flag_manager.apply_flags_hybrid.
            latest = deployment.get_latest_production_guid()
            if latest:
                if (not installed or installed == "unknown" or installed != latest):
                    root = RobloxManager.resolve_download_versions_root()
                    cache = RobloxManager.get_all_roblox_version_dirs() or []
                    if root:
                        log(f"[*] Syncing Roblox to latest production build ({latest}) "
                            "before join...", (100, 255, 255))
                        result = fixer.run_upgrade(latest, root, cache)
                        if result.get("ok"):
                            target = latest
                else:
                    target = latest
                _prune_stock_if_closed(latest)
        except Exception as e:
            log(f"[!] Pre-join update skipped: {e}", (255, 200, 100))

        # Flags AFTER any upgrade so a freshly created version folder gets
        # ClientAppSettings.json. Missing keys after retry block the join.
        launch_guid = target or installed
        if not _ensure_startup_flags(launch_guid):
            return
        try:
            ok, _ = rm.launch_specific_version(launch_guid, args=uri)
            if not ok and target != installed:
                # Target build isn't runnable - never lose the join; use installed
                # after confirming that folder also has the flags.
                if _ensure_startup_flags(installed):
                    rm.launch_specific_version(installed, args=uri)
        except Exception as e:
            log(f"[!] Pinned launch failed, falling back: {e}", (255, 120, 120))
    finally:
        _restore_third_party_if_transient()


def bootstrap_join(uri):
    """Standalone path (full app NOT running): version-sync, then write
    file-based flags, then launch. Flags live inside launch_join so a newly
    created version folder gets ClientAppSettings.json before the player starts.
    This is what makes clicking Play apply flags without opening FFM."""
    launch_join(uri)
