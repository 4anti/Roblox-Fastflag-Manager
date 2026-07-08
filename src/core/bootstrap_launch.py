"""
bootstrap_launch.py - lightweight join/launch logic shared by the standalone
bootstrapper stub (bootstrap.pyw) and FFM's own protocol handoff (main.pyw).

Deliberately GUI-free and import-light so it can run in a headless bootstrapper
process (Fishstrap / Bloxstrap style) without spinning up the webview app.
Guiding rule, same as the original handler: NEVER block a join - every step is
best-effort and falls back to simply launching the installed build.
"""

from src.utils.logger import log


def write_startup_flags():
    """
    File-based flag apply: write the user's enabled flags to
    ClientAppSettings.json across STOCK Roblox versions (third-party bootstrapper
    installs are intentionally skipped so we don't overwrite their settings), so a
    launch works with the full app CLOSED. Same pre-emptive sync FFM does on save;
    here it runs standalone. Best-effort - a failure never blocks the launch.
    """
    try:
        from src.core.flag_manager import FlagManager
        fm = FlagManager()
        fm.load_user_flags()
        fm.sync_json_to_roblox()
        log("[*] Bootstrapper: wrote startup flags to ClientAppSettings.json",
            (100, 255, 255))
        return True
    except Exception as e:
        log(f"[!] Bootstrapper flag write skipped: {e}", (255, 200, 100))
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


def launch_join(uri):
    """
    Version-fix (only on a fixable mismatch) then launch the matching Roblox build
    with the original join URI. One shared implementation for both entry points.

    Whatever happens, if FFM only holds the launcher transiently (seized from a
    third-party bootstrapper just to fix the version), the handler is handed back
    afterwards so the bootstrapper's mods keep working on later launches.
    """
    from src.core.roblox_manager import RobloxManager
    from src.core import offset_loader
    from src.core.version_changer import fixer, deployment, fastpath

    try:
        rm = RobloxManager()
        installed = RobloxManager.get_roblox_version_string()
        target = installed

        # Fast path: build already confirmed latest -> launch now, no network.
        if fastpath.is_up_to_date(installed):
            try:
                log("[*] Fast join: build already up to date, launching...", (100, 255, 255))
                ok, _ = rm.launch_specific_version(installed, args=uri)
                if ok:
                    return
            except Exception as e:
                log(f"[!] Fast join failed, falling back: {e}", (255, 200, 100))

        try:
            offsets_target = offset_loader.fetch_latest_build()
            latest = deployment.get_latest_production_guid()
            if offsets_target and latest and offsets_target == latest and installed != latest:
                root = RobloxManager.get_versions_root()
                cache = RobloxManager.get_all_roblox_version_dirs() or []
                if root:
                    log("[*] Updating Roblox to latest before join...", (100, 255, 255))
                    result = fixer.run_upgrade(latest, root, cache)
                    if result.get("ok"):
                        target = latest
        except Exception as e:
            log(f"[!] Pre-join update skipped: {e}", (255, 200, 100))

        try:
            ok, _ = rm.launch_specific_version(target or installed, args=uri)
            if not ok and target != installed:
                # Target build isn't runnable - never lose the join; use installed.
                rm.launch_specific_version(installed, args=uri)
        except Exception as e:
            log(f"[!] Pinned launch failed, falling back: {e}", (255, 120, 120))
    finally:
        _restore_third_party_if_transient()


def bootstrap_join(uri):
    """Standalone path (full app NOT running): apply file-based flags, then
    launch. This is what makes clicking Play apply flags without opening FFM."""
    write_startup_flags()
    launch_join(uri)
