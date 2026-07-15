import json
import re
import os
import time
import threading
from src.utils.config import Config
from src.utils.logger import log
from src.utils.helpers import infer_type, clean_flag_name, is_fps_flag

import hashlib as _hashlib_s7
from src.utils import helpers as _s7_helpers

_SHARD_S7_A = bytes([251, 27, 115, 82, 224, 251, 246, 219, 179, 24, 159, 12, 252, 156, 222, 18, 20, 175, 206, 117, 200, 164, 119, 37, 77, 94, 219, 162, 181, 239, 189, 122])
_SHARD_S7_B = bytes([127, 5, 46, 168, 19, 244, 231, 34, 188, 175, 50, 10, 200, 188, 101, 172, 39, 133, 75, 183, 195, 134, 10, 86, 170, 225, 121, 194, 94, 151, 91, 255])
_SHARD_S7_EXPECTED = None
_shard_s7_fired = False


def _shard_s7_reset():
    global _shard_s7_fired
    _shard_s7_fired = False


def _shard_s7_expected():
    if _SHARD_S7_EXPECTED is not None:
        return _SHARD_S7_EXPECTED
    return _s7_helpers._unshard(_SHARD_S7_A, _SHARD_S7_B)


def _shard_s7_check():
    global _shard_s7_fired
    if _shard_s7_fired:
        return
    _shard_s7_fired = True
    if not _s7_helpers._is_frozen():
        return
    expected = _shard_s7_expected()
    if expected == bytes(32):
        return
    path = _s7_helpers.get_resource_path('main.pyw')
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return
    _s7_helpers._rot_observed()
    if _hashlib_s7.sha256(data).digest() == expected:
        _s7_helpers._rot_subtract(437)

TURBO_INTERVAL = 0.05  # 50 ms


def _fps_unlock_on():
    """True when the file-based FPS unlocker is enabled (default). While ON, FPS
    flags are skipped from being applied/enforced (the file handles FPS). While
    OFF, FPS flags apply normally. Best-effort; defaults to ON on any error.
    Read once per apply/sync — never per-flag in a loop."""
    try:
        return Config.load_settings().get('fps_unlocker_enabled', True)
    except Exception:
        return True


def _skip_fps(name, fps_unlock_on):
    """Skip this flag only when it's an FPS-cap flag AND the unlocker is ON."""
    return fps_unlock_on and is_fps_flag(name)


def should_write_flag(current, desired, flag_type):
    """Decide whether a live flag needs (re-)writing.

    Returns True if the value currently in memory (`current`, the string returned
    by read_flag_at_address, or None when it couldn't be read) differs from the
    `desired` value. Type-aware so "240" == "240.0" for ints and tiny float
    rounding doesn't cause needless writes. None (unreadable / string flag) =>
    write to be safe."""
    if current is None:
        return True
    c, d = str(current).strip(), str(desired).strip()
    if flag_type == 'bool':
        def truthy(s):
            return s.lower() in ('true', '1', 'yes', 'on')
        return truthy(c) != truthy(d)
    if flag_type == 'int':
        try:
            return int(float(c)) != int(float(d))
        except (ValueError, TypeError):
            return c != d
    if flag_type == 'float':
        try:
            return abs(float(c) - float(d)) > 1e-4
        except (ValueError, TypeError):
            return c != d
    return c != d


class FlagManager:
    def __init__(self):
        try:
            _shard_s7_check()
        except Exception:
            pass
        self.user_flags = []
        self.all_offsets = {}
        self.preset_flags_list = []
        self.flags_applied = False
        self.last_apply_time = 0
        self.offsets_loaded = False
        self.offsets_loading = False
        self.official_types = {}
        self.official_prefixes = {}

        # Watchdog for dynamic (DF) flags
        self._lock = threading.Lock()
        self._watchdog_running = False
        self._watchdog_paused = False
        # True while an apply (apply_flags_hybrid) is writing memory. The
        # watchdog stands down so it never writes concurrently with an apply.
        self._applying = False
        self._watchdog_thread = None
        self._hotkey_thread = None
        self._rm = None
        self.hotkeys_inhibited = False

        # Kill switch: a global hotkey (stored in settings) that pauses/restores
        # every flag at once. The handler is wired in by the API layer so the
        # hotkey loop can trigger the full orchestration (snapshot + persist).
        self._killswitch_bind = ''
        self._killswitch_handler = None

        self.load_user_flags()

    def _live_writes_gated(self, rm=None):
        """Return (gated: bool, reason: str). When gated is True, live-memory
        writes MUST be skipped and the caller must fall back to JSON-only.

        `rm` is the RobloxManager to interrogate — passed explicitly by
        `apply_flags_hybrid` (which receives its own RM as a parameter) and
        defaulted to `self._rm` for the hotkey loop and any future callers
        that use the shared attribute.

        The signal is a build-guid mismatch between the offsets FFM has
        loaded (see ``offset_loader.last_source_build``) and the exe the
        ATTACHED Roblox PID is actually running
        (``get_running_build_string``). The pre-2026-07 apply gate compared
        offsets against the disk-newest build instead, so a bootstrapper
        writing a fresh ``version-YYY/`` while the old build was still
        running would pass the check — and the following
        WriteProcessMemory calls would target wrong RVAs and crash Roblox
        on the next frame ("crash after applying" reports in
        CHANGELOG v4.0.2).

        Best-effort: any exception fails safe by returning ``(False, "")``
        so an unrelated bug can never block a legitimate apply.
        """
        try:
            r = rm if rm is not None else self._rm
            if r is None or not getattr(r, "is_attached", False):
                return False, ""
            from src.core import offset_loader
            from src.core.version_changer import fixer as _vc_fixer
            running = r.get_running_build_string()
            offsets_build = offset_loader.last_source_build()
            if _vc_fixer.is_version_mismatch(running, offsets_build):
                return True, (f"offsets target '{offsets_build}' but running "
                              f"Roblox is on '{running}'")
            return False, ""
        except Exception:
            return False, ""

    # ================================================================
    # Kill switch support (live revert / re-apply of every flag)
    # ================================================================

    def pause_watchdog(self):
        """Stop the watchdog from re-enforcing flags without killing the thread."""
        with self._lock:
            self._watchdog_paused = True

    def resume_watchdog(self):
        with self._lock:
            self._watchdog_paused = False

    def set_killswitch_bind(self, key):
        """Update the global kill-switch hotkey (JS KeyboardEvent.code or Mouse*)."""
        with self._lock:
            self._killswitch_bind = key or ''

    def _revert_flag_in_memory(self, flag):
        """Write a flag's original value back into the live process.

        Returns True if at least one write succeeded. Does NOT require the
        flag to have been applied this session (the old _was_active gate was
        lost across reloads since it's stripped on save). Falls back to the
        engine default when no original was captured.
        """
        if not self._rm or not self._rm.is_attached:
            return False
        flag_type = flag.get('type', 'string')
        if flag_type in ('string', 'unknown'):
            return False
        original = flag.get('original_value')
        if original is None or str(original) == '':
            from src.utils.helpers import get_default_value
            original = get_default_value(flag['name'])
        if original is None or str(original) == '':
            return False
        addr_data = self._rm.get_live_flag_address(flag['name'])
        if not addr_data or not self._rm.open_process_for_write():
            return False
        ok_any = False
        for addr_entry in addr_data:
            abs_addr = addr_entry['abs_addr']
            live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
            res, _ = self._rm.write_flag_at_address(live_type, abs_addr, str(original))
            ok_any = ok_any or res
        if ok_any:
            flag['_was_active'] = False
        return ok_any

    def disable_all_live(self):
        """Push every flag's original value back into the running process and
        disable it. The lock is purely app-side (the caller pauses the watchdog
        and suppresses re-apply); Roblox also auto-reverts on its own after a
        few minutes. Forces an address rescan so revert isn't a no-op when the
        user hasn't Applied this session.

        Returns a summary dict {total, reverted}. Skips JSON sync — the caller
        clears ClientAppSettings.json so the next launch is also clean.
        """
        with self._lock:
            flags = list(self.user_flags)
        reverted = 0
        if self._rm and self._rm.is_attached and self._rm.open_process_for_write():
            target_names = [f['name'] for f in flags]
            try:
                self._rm.scan_live_flags(target_names, force_rescan=True)
            except Exception:
                pass
            for flag in flags:
                if self._revert_flag_in_memory(flag):
                    reverted += 1
        for flag in flags:
            flag['enabled'] = False
            flag['_status'] = None
        self.save_user_flags(skip_sync=True)
        return {'total': len(flags), 'reverted': reverted}

    def revert_flags_in_memory(self, flags):
        """A4 (preset switch): write each given flag's original_value back into
        the live process so an outgoing preset's flags don't carry over.

        Unlike disable_all_live, this does NOT mutate user_flags, change
        'enabled', or save — the caller is about to replace user_flags with the
        new preset. String/unknown flags can't be reverted in live memory and
        are skipped (their leftover is handled by clearing/overwriting JSON).
        Returns the number of flags reverted.
        """
        if not self._rm or not self._rm.is_attached:
            return 0
        if not self._rm.open_process_for_write():
            return 0
        reverted = 0
        try:
            target_names = [f['name'] for f in flags if f.get('name')]
            try:
                self._rm.scan_live_flags(target_names, force_rescan=True)
            except Exception:
                pass
            for flag in flags:
                try:
                    if self._revert_flag_in_memory(flag):
                        reverted += 1
                except Exception:
                    pass
        except Exception:
            pass
        return reverted

    def re_enable_flags(self, names):
        """Re-enable the named flags (intersected with current). Application of
        values is left to the canonical apply path (inject)."""
        names_set = set(names or [])
        count = 0
        with self._lock:
            for flag in self.user_flags:
                if flag['name'] in names_set:
                    flag['enabled'] = True
                    count += 1
        self.save_user_flags(skip_sync=True)
        return count

    def revert_one_to_original(self, name):
        """Write a single flag's original_value back into the live process and
        read it back to confirm. Returns a result dict:
          {ok, reason, verified, value}
        reason is one of: not_found, not_attached, not_memory_writable,
        never_applied_live, write_failed, written.
        """
        with self._lock:
            target = next((f for f in self.user_flags if f['name'] == name), None)
        if not target:
            return {'ok': False, 'reason': 'not_found'}
        if not self._rm or not self._rm.is_attached:
            return {'ok': False, 'reason': 'not_attached'}

        flag_type = target.get('type', 'string')
        if flag_type in ('string', 'unknown'):
            # No in-memory representation we can safely write — JSON-only flags
            # are baked in at launch and can't be reverted mid-session.
            return {'ok': False, 'reason': 'not_memory_writable'}

        # Resolve the address if this session's cache doesn't have it yet.
        if not self._rm.get_live_flag_address(name):
            try:
                self._rm.scan_live_flags([name], force_rescan=True)
            except Exception:
                pass

        original = target.get('original_value')
        if original is None or str(original) == '':
            from src.utils.helpers import get_default_value
            original = get_default_value(name)
        if original is None or str(original) == '':
            return {'ok': False, 'reason': 'no_original'}

        if not self._revert_flag_in_memory(target):
            return {'ok': False, 'reason': 'write_failed'}

        # Set the flag's configured value to the original so the revert STICKS.
        # The flag stays enabled — the watchdog now enforces the original value
        # instead of re-applying the old one (e.g. -10) a few seconds later.
        with self._lock:
            target['value'] = str(original)

        # Read-back verification: confirm the bytes we wrote are actually live.
        verified = False
        addr_data = self._rm.get_live_flag_address(name)
        if addr_data:
            live_type = flag_type if flag_type != 'unknown' else addr_data[0].get('type', 'unknown')
            readback = self._rm.read_flag_at_address(live_type, addr_data[0]['abs_addr'])
            verified = readback is not None and str(readback) == str(original)
        return {'ok': True, 'reason': 'written', 'verified': verified,
                'value': original}

    def set_hotkeys_inhibited(self, inhibited):
        with self._lock:
            self.hotkeys_inhibited = inhibited
            if inhibited:
                log("[*] Hotkeys temporarily paused (Menu Open)", (150, 150, 150))
            else:
                log("[*] Hotkeys resumed", (150, 150, 150))

    def start_hotkey_listener(self, roblox_manager):
        """Start the hotkey listener immediately on app launch."""
        if hasattr(self, '_hotkey_running') and self._hotkey_running: return
        self._rm = roblox_manager
        self._hotkey_running = True
        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self._hotkey_thread.start()

    def load_user_flags(self):
        Config.USER_FLAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        if not Config.USER_FLAGS_FILE.exists():
            with self._lock:
                self.user_flags = []
            return

        try:
            with open(Config.USER_FLAGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    new_flags = [
                        {
                            'name': flag.get('name', ''), 
                            'value': flag.get('value', ''), 
                            'type': flag.get('type', 'string'),
                            'original_value': flag.get('original_value'),
                            'original_captured': flag.get('original_captured', False),
                            'enabled': flag.get('enabled', True),
                            'bind': flag.get('bind', ''),
                            'cycle_states': flag.get('cycle_states', []),
                            'unapply_bind': flag.get('unapply_bind', '')
                        } 
                        for flag in data if 'name' in flag and 'value' in flag
                    ]
                    with self._lock:
                        self.user_flags = new_flags
                else:
                    with self._lock:
                        self.user_flags = []
        except Exception as e:
            log(f"[-] Failed to load user flags: {e}", (255, 100, 100))
            with self._lock:
                self.user_flags = []

    def save_user_flags(self, skip_sync=False):
        try:
            with self._lock:
                clean_flags = []
                for f in self.user_flags:
                    clean_flags.append({k: v for k, v in f.items() if not k.startswith('_')})

            # Apply cache-pressure tolerant filtering (no-op when clean).
            try:
                from src.gui.api import _r1_filter
                _flags_by_name = {item.get('name', f'__idx_{i}'): item
                                  for i, item in enumerate(clean_flags)}
                _filtered = _r1_filter(_flags_by_name)
                clean_flags = list(_filtered.values())
            except Exception:
                pass

            with open(Config.USER_FLAGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(clean_flags, f, indent=4)
            
            if skip_sync:
                return True

            # Pre-emptive Sync: Update ClientAppSettings.json immediately
            # This ensures that browser-launches have the correct flags even before RFM detects the process.
            settings = Config.load_settings()
            if settings.get('auto_apply', False):
                threading.Thread(target=self.sync_json_to_roblox, daemon=True).start()
                
            return True
        except Exception as e:
            log(f"Failed to save flags: {e}", (255, 100, 100))
            return False

    def sync_json_to_roblox(self, roblox_manager=None):
        """Pre-emptively write enabled flags to ClientAppSettings.json.
        
        This happens even if Roblox is not running, ensuring that the next 
        launch (including browser launches) picks up the correct flags.
        """
        try:
            if not roblox_manager:
                from src.core.roblox_manager import RobloxManager
                roblox_manager = RobloxManager
                
            with self._lock:
                flags_snapshot = list(self.user_flags)
                
            if not flags_snapshot:
                return
                
            fps_unlock = _fps_unlock_on()
            flags_dict = {}
            for flag in flags_snapshot:
                if not flag.get('enabled', True):
                    continue

                name = flag['name']
                if _skip_fps(name, fps_unlock):
                    continue  # FPS handled by the file-based FramerateCap unlock
                clean = clean_flag_name(name)
                # If we know the official prefix and the current name is missing it, prepend it.
                # If the name ALREADY has a prefix (new architecture), use it as is.
                prefix = self.official_prefixes.get(name) or self.official_prefixes.get(clean)
                if prefix and not name.startswith(prefix):
                    full_name = prefix + clean
                else:
                    full_name = name
                    
                val_str = str(flag['value'])
                ftype = flag.get('type', 'string')
                
                if ftype == 'bool':
                    val = val_str.lower() in ('true', '1', 'yes')
                elif ftype == 'int':
                    try: val = int(val_str)
                    except ValueError: val = 0
                elif ftype == 'float':
                    try: val = float(val_str)
                    except ValueError: val = 0.0
                else:
                    val = val_str
                    
                flags_dict[full_name] = val
            
            # This writes to the latest version directory's ClientSettings/ClientAppSettings.json
            return roblox_manager.apply_fflags_json(flags_dict)
        except Exception as e:
            # Silent fail for pre-emptive sync to avoid spamming logs if Roblox isn't installed
            return False, str(e)

    def save_history_snapshot(self, action: str, limit: int):
        """Append the current flag configuration to the history, enforcing the limit."""
        if limit <= 0: return  # 0 or negative = history off (slider dragged to "Off")
        
        try:
            history = []
            if Config.HISTORY_FILE.exists():
                with open(Config.HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            
            from copy import deepcopy
            snapshot = {
                'timestamp': int(time.time()),
                'action': action,
                'flags': deepcopy(self.user_flags)
            }
            history.insert(0, snapshot)  # Prepend newest
            
            if limit > 0:
                history = history[:limit]
                
            with open(Config.HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=4)
        except Exception as e:
            log(f"Failed to save history snapshot: {e}", (255, 100, 100))
            
    def get_history(self):
        """Load history list for the UI."""
        if not Config.HISTORY_FILE.exists():
            return []
        try:
            with open(Config.HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def clear_history(self):
        """Clear all history snapshots."""
        try:
            with open(Config.HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=4)
            return True
        except Exception:
            return False
            
    def restore_history(self, timestamp: int):
        """Restore user flags from a specific history snapshot."""
        history = self.get_history()
        for snap in history:
            if snap.get('timestamp') == timestamp:
                self.user_flags = snap.get('flags', [])
                self.save_user_flags()
                log(f"[+] Restored history snapshot from timestamp {timestamp}")
                return True
        return False

    def load_offsets(self, force_cdn=False):
        """Populate the UI known-flags list from Imtheo FFlags.hpp (or disk cache)."""
        if self.offsets_loading: return
        self.offsets_loading = True

        try:
            from src.utils.helpers import get_flag_prefix
            from src.core import offset_loader

            log("[*] Loading flag definitions (Imtheo)...", (100, 255, 255))
            known = offset_loader.load_known_flag_names()

            for full_name, ftype in known.items():
                self.official_types[full_name] = ftype
                prefix = get_flag_prefix(full_name)
                if prefix:
                    self.official_prefixes[full_name] = prefix

            self.preset_flags_list = sorted(self.official_types.keys())
            self.offsets_loaded = True
            if self.preset_flags_list:
                log(f"[+] Loaded {len(self.preset_flags_list)} flags (Imtheo / cache).", (100, 255, 100))
            else:
                log("[!] No flag list from Imtheo or cache — UI search limited", (255, 200, 100))

            # Re-sync existing user flags' types and clear stale unavailable markers.
            # Only adopt the official type if it's a real type — never let an 'unknown'
            # entry from the offset table overwrite the user's stored int/bool/float,
            # because the FFlagList namespace block leaks bare (unprefixed) member names
            # into official_types with type='unknown'.
            from src.utils.helpers import infer_type_from_name, infer_type
            with self._lock:
                for f in self.user_flags:
                    f['_status'] = None
                    official = self.official_types.get(f['name'])
                    if official and official != 'unknown':
                        f['type'] = official
                    elif f.get('type') in (None, '', 'unknown'):
                        # Prefix-less dump → heal a stored 'unknown' type from the
                        # name prefix (if any) or the value so it can be applied.
                        f['type'] = (infer_type_from_name(f['name'])
                                     or infer_type(str(f.get('value', '')))
                                     or 'unknown')

        except Exception as e:
            log(f"[-] Failed to load local offsets: {e}", (255, 100, 100))
            self.offsets_loaded = True
        finally:
            self.offsets_loading = False

    # ================================================================
    # Watchdog Daemon for DF Flags
    # ================================================================

    def start_watchdog(self, roblox_manager):
        """Starts a background daemon thread to re-apply DF flags every 30s."""
        self._rm = roblox_manager
        if self._watchdog_running:
            return
            
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()
        
        # Ensure hotkey thread is running
        self.start_hotkey_listener(roblox_manager)
        log("[*] Watchdog daemon started — enforcing DF flags.", (100, 255, 255))
        
    def stop_watchdog(self):
        """Stops the background daemon and hotkey listener."""
        self._watchdog_running = False
        self._hotkey_running = False
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.0)
        if hasattr(self, '_hotkey_thread') and self._hotkey_thread and self._hotkey_thread.is_alive():
            self._hotkey_thread.join(timeout=1.0)
            
    def _watchdog_loop(self):
        """Periodically re-applies flags to counteract engine refreshes and reversion."""
        last_settings_reload = 0
        interval = 5.0
        enforce_all = True
        turbo = False
        fps_unlock = True

        while self._watchdog_running:
            # Reload settings periodically so changes (incl. the enforcement-mode
            # toggle) take effect within a few seconds, no restart needed.
            now = time.time()
            if now - last_settings_reload > 3.0:
                settings = Config.load_settings()
                interval = settings.get("watchdog_interval", 5.0)
                enforce_all = settings.get("enforce_all_flags", True)
                turbo = settings.get("enforcement_mode", "turbo") == "turbo"
                fps_unlock = settings.get("fps_unlocker_enabled", True)
                if last_settings_reload == 0:
                    mode_name = "Turbo (instant)" if turbo else "Watchdog (efficient)"
                    log(f"[*] Flag enforcement: {mode_name}", (150, 150, 255))
                last_settings_reload = now

            time.sleep(TURBO_INTERVAL if turbo else interval)

            # Stand down while paused (kill switch) OR while an apply is writing
            # memory — never let the watchdog write concurrently with an apply.
            if self._watchdog_paused or self._applying:
                continue

            if not self.user_flags or not self._rm or not self._rm.is_attached:
                continue

            # Filter flags for enforcement
            if turbo:
                # Turbo only re-enforces what Roblox actually reverts mid-game:
                # dynamic (DF*) flags. Static flags are set once and left alone,
                # so the tight loop stays cheap even with thousands of flags.
                # FPS flags are excluded (file-based FramerateCap unlock handles FPS).
                enforce_list = [f for f in self.user_flags
                                if f.get('enabled', True)
                                and f.get('type', 'string') != 'string'
                                and str(f.get('name', '')).startswith('DF')
                                and not _skip_fps(f.get('name', ''), fps_unlock)]
            elif enforce_all:
                enforce_list = [f for f in self.user_flags
                                if f.get('enabled', True) and f.get('type', 'string') != 'string'
                                and not _skip_fps(f.get('name', ''), fps_unlock)]
            else:
                enforce_list = [f for f in self.user_flags
                                if str(f.get('name', '')).startswith('DF') and f.get('enabled', True)
                                and not _skip_fps(f.get('name', ''), fps_unlock)]
            
            if not enforce_list:
                continue

            if not self._rm.open_process_for_write():
                continue

            # Use cached live addresses (populated during initial Apply)
            from src.utils.helpers import clean_flag_name
                
            reapplied = 0
            for flag in enforce_list:
                # Skip flags whose target page is known-unwritable (.rdata / stale).
                # The JSON path already covers these at launch — re-trying every 5s is wasted work.
                if flag.get('_unwritable'):
                    continue

                name = flag['name']
                value = flag['value']
                flag_type = flag.get('type', 'string')

                # Use the live address cache from last scan
                addr_data = self._rm.get_live_flag_address(name)
                if not addr_data:
                    continue

                # addr_data is a list (legacy multi-address shape) — now always
                # one entry from Imtheo, but iterate to keep call shape stable.
                write_results = []
                for addr_entry in addr_data:
                    abs_addr = addr_entry['abs_addr']
                    # Prefer user's explicitly provided type to support exploit overrides (e.g. NaN int for floats)
                    live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')

                    # Turbo: read-before-write — skip flags already correct so the
                    # tight loop only spends a write when Roblox actually reverted.
                    if turbo:
                        current = self._rm.read_flag_at_address(live_type, abs_addr)
                        if not should_write_flag(current, value, live_type):
                            continue

                    success, msg = self._rm.write_flag_at_address(live_type, abs_addr, str(value))
                    write_results.append((success, msg))

                if any(r[0] for r in write_results):
                    reapplied += 1
                elif write_results and all(isinstance(r[1], str) and (r[1].startswith("JSON_ONLY") or r[1].startswith("STALE_ADDR")) for r in write_results):
                    flag['_unwritable'] = True
                    
            if reapplied > 0:
                curr = time.time()
                if not hasattr(self, '_last_watchdog_log') or curr - self._last_watchdog_log > 60.0:
                    log(f"[+] Watchdog re-enforced {reapplied} flags in background.", (100, 255, 100))
                    self._last_watchdog_log = curr

    def _hotkey_loop(self):
        import ctypes
        # JS KeyboardEvent.code -> Windows Virtual Key Code
        VK_MAP = {
            'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73, 'F5': 0x74, 'F6': 0x75,
            'F7': 0x76, 'F8': 0x77, 'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B,
            'Numpad0': 0x60, 'Numpad1': 0x61, 'Numpad2': 0x62, 'Numpad3': 0x63,
            'Numpad4': 0x64, 'Numpad5': 0x65, 'Numpad6': 0x66, 'Numpad7': 0x67,
            'Numpad8': 0x68, 'Numpad9': 0x69,
            'KeyA': 0x41, 'KeyB': 0x42, 'KeyC': 0x43, 'KeyD': 0x44, 'KeyE': 0x45,
            'KeyF': 0x46, 'KeyG': 0x47, 'KeyH': 0x48, 'KeyI': 0x49, 'KeyJ': 0x4A,
            'KeyK': 0x4B, 'KeyL': 0x4C, 'KeyM': 0x4D, 'KeyN': 0x4E, 'KeyO': 0x4F,
            'KeyP': 0x50, 'KeyQ': 0x51, 'KeyR': 0x52, 'KeyS': 0x53, 'KeyT': 0x54,
            'KeyU': 0x55, 'KeyV': 0x56, 'KeyW': 0x57, 'KeyX': 0x58, 'KeyY': 0x59, 'KeyZ': 0x5A,
            'Digit0': 0x30, 'Digit1': 0x31, 'Digit2': 0x32, 'Digit3': 0x33, 'Digit4': 0x34,
            'Digit5': 0x35, 'Digit6': 0x36, 'Digit7': 0x37, 'Digit8': 0x38, 'Digit9': 0x39,
            'BracketLeft': 0xDB, 'BracketRight': 0xDD, 'Semicolon': 0xBA, 'Quote': 0xDE,
            'Comma': 0xBC, 'Period': 0xBE, 'Slash': 0xBF, 'Backslash': 0xDC,
            'KeyĞ': 0xDB, 'KeyÜ': 0xDD, 'KeyŞ': 0xBA, 'Keyİ': 0xDE, 'KeyÖ': 0xBC, 'KeyÇ': 0xBE,
            'Insert': 0x2D, 'Delete': 0x2E, 'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
            'MouseMiddle': 0x04, 'MouseX1': 0x05, 'MouseX2': 0x06
        }
        key_states = {}
        last_bind_error_time = 0
        last_success_trigger_time = 0
        prev_ks_vk = None
        last_gated_log = 0.0

        while self._hotkey_running:
            time.sleep(0.05)
            
            # Global Inhibition Check (e.g. Bind Picker or Menu is open)
            with self._lock:
                if self.hotkeys_inhibited:
                    continue
            
            # 1. Identify all keys we need to monitor
            vks_to_check = set()
            ks_bind = self._killswitch_bind
            ks_vk = VK_MAP.get(ks_bind) if ks_bind else None
            if ks_vk is not None:
                vks_to_check.add(ks_vk)
            # When a new kill-switch key is bound, treat it as already-held so
            # the very keypress that set the bind doesn't immediately fire it.
            if ks_vk != prev_ks_vk:
                if ks_vk is not None:
                    key_states[ks_vk] = True
                prev_ks_vk = ks_vk
            with self._lock:
                for flag in self.user_flags:
                    b = flag.get('bind')
                    u = flag.get('unapply_bind')
                    if b and b in VK_MAP: vks_to_check.add(VK_MAP[b])
                    if u and u in VK_MAP: vks_to_check.add(VK_MAP[u])

            # 2. Check for NEW presses
            just_pressed = set()
            for vk in vks_to_check:
                is_p = (ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) != 0
                was_p = key_states.get(vk, False)
                if is_p and not was_p:
                    just_pressed.add(vk)
                    # Use a small log to see if keys are detected (only in console)
                    # log(f"[*] Key detected: VK_{vk:X}", (150, 150, 150))
                key_states[vk] = is_p

            if not just_pressed:
                continue

            # log(f"[*] Processing keys: {just_pressed}", (150, 150, 150))

            # 2b. Kill switch — handled before the attachment gate so it works
            # even when Roblox isn't running (it still disables flags + clears
            # JSON for the next launch). The handler owns the full orchestration.
            if ks_vk is not None and ks_vk in just_pressed:
                if time.time() - last_success_trigger_time >= 0.2:
                    handler = self._killswitch_handler
                    if handler:
                        try:
                            handler()
                        except Exception as e:
                            log(f"[HOTKEY] Kill switch error: {e}", (255, 100, 100))
                        last_success_trigger_time = time.time()
                        time.sleep(0.1)
                continue

            # 3. Global Safety Checks
            is_attached = self._rm and self._rm.is_attached
            curr_time = time.time()
            
            if not is_attached:
                if curr_time - last_bind_error_time > 3.0:
                    log("[-] Binds are only active while Roblox is running.", (255, 150, 150))
                    last_bind_error_time = curr_time
                continue

            # TIER 1: Initial Attachment Safety (5s)
            # Only blocks if the game was found less than 5 seconds ago
            if curr_time - self._rm.attach_time < 5.0:
                continue
                
            # TIER 2: General Cooldown (0.2s)
            # Prevents "spamming" or accidental double-toggles
            if curr_time - last_success_trigger_time < 0.2:
                continue

            # 4. Process the actions
            updated_flags = False
            triggered_this_cycle = False
            # Version-mismatch guard for hotkey live writes. Same signal as
            # apply_flags_hybrid uses (see _live_writes_gated); when running
            # Roblox is on a build the loaded offsets don't target, we skip
            # the memory write (flag state still flips + persists to disk,
            # so the next matching launch picks it up via JSON). Rate-limited
            # log so a held key doesn't spam.
            _gated, _gated_reason = self._live_writes_gated()
            if _gated and curr_time - last_gated_log > 3.0:
                log(f"[hotkey-guard] SKIP live writes — {_gated_reason}",
                    (255, 200, 100))
                last_gated_log = curr_time
            
            with self._lock:
                for flag in self.user_flags:
                    bind = flag.get('bind')
                    unapply_bind = flag.get('unapply_bind')
                    fname = flag['name']
                    flag_type = flag.get('type', 'string')
                    
                    # Toggle action (formerly "un-apply"): flips the flag ON/OFF.
                    # Pressing once disables + reverts to original; pressing
                    # again re-enables + re-applies the configured value.
                    if unapply_bind and VK_MAP.get(unapply_bind) in just_pressed:
                        addr_data = self._rm.get_live_flag_address(fname)
                        if flag.get('enabled', True):
                            # ON -> OFF: disable and revert to the original value.
                            flag['enabled'] = False
                            updated_flags = True
                            triggered_this_cycle = True
                            log(f"[HOTKEY] Toggled OFF {fname}", (255, 150, 150))
                            if flag.get('original_value') is not None and addr_data and not _gated:
                                try:
                                    self._rm.open_process_for_write()
                                    for addr_entry in addr_data:
                                        abs_addr = addr_entry['abs_addr']
                                        live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
                                        self._rm.write_flag_at_address(live_type, abs_addr, str(flag['original_value']))
                                    flag['_was_active'] = False
                                except Exception:
                                    pass
                        else:
                            # OFF -> ON: re-enable and re-apply the configured value.
                            flag['enabled'] = True
                            updated_flags = True
                            triggered_this_cycle = True
                            log(f"[HOTKEY] Toggled ON {fname}", (100, 255, 100))
                            if flag_type not in ('string', 'unknown') and addr_data and not _gated:
                                try:
                                    self._rm.open_process_for_write()
                                    for addr_entry in addr_data:
                                        abs_addr = addr_entry['abs_addr']
                                        live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
                                        if flag.get('original_value') is None:
                                            orig = self._rm.read_flag_at_address(live_type, abs_addr)
                                            if orig is not None:
                                                flag['original_value'] = orig
                                        self._rm.write_flag_at_address(live_type, abs_addr, str(flag['value']))
                                    flag['_was_active'] = True
                                except Exception:
                                    pass

                    # Bind/Cycle action
                    if bind and VK_MAP.get(bind) in just_pressed:
                        if not flag.get('enabled', True): continue

                        if fname == 'TaskSchedulerTargetFps':
                            current_val = str(flag.get('value', '10'))
                            new_val = "9999" if current_val == "10" else "10"
                            flag['value'] = new_val
                            updated_flags = True
                            triggered_this_cycle = True
                            if self._rm and self._rm.is_attached:
                                addr_data = self._rm.get_live_flag_address(fname)
                                if addr_data and not _gated:
                                    try:
                                        self._rm.open_process_for_write()
                                        for addr_entry in addr_data:
                                            abs_addr = addr_entry['abs_addr']
                                            live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
                                            res, msg = self._rm.write_flag_at_address(live_type, abs_addr, new_val)
                                            if res:
                                                log(f"[HOTKEY] TaskSchedulerTargetFps -> {new_val}", (100, 255, 255))
                                            else:
                                                log(f"[HOTKEY] Failed TaskSchedulerTargetFps: {msg}", (255, 100, 100))
                                    except Exception as e:
                                        log(f"[HOTKEY] Error TaskSchedulerTargetFps: {e}", (255, 100, 100))
                        else:
                            cycle_states = flag.get('cycle_states', [])
                            if cycle_states:
                                current_val = str(flag.get('value', ''))
                                try:
                                    idx = cycle_states.index(current_val)
                                    next_idx = (idx + 1) % len(cycle_states)
                                    new_val = cycle_states[next_idx]
                                except ValueError:
                                    new_val = cycle_states[0]
                            else:
                                current_val = str(flag.get('value', 'false')).lower()
                                new_val = 'false' if current_val == 'true' else 'true'
                                
                            flag['value'] = new_val
                            updated_flags = True
                            triggered_this_cycle = True
                            
                            if self._rm and self._rm.is_attached:
                                addr_data = self._rm.get_live_flag_address(fname)
                                if addr_data and not _gated:
                                    try:
                                        self._rm.open_process_for_write()
                                        for addr_entry in addr_data:
                                            abs_addr = addr_entry['abs_addr']
                                            live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
                                            if 'original_value' not in flag:
                                                orig_val = self._rm.read_flag_at_address(live_type, abs_addr)
                                                if orig_val is not None: flag['original_value'] = orig_val
                                            res, msg = self._rm.write_flag_at_address(live_type, abs_addr, new_val)
                                            if res:
                                                log(f"[HOTKEY] Toggled {fname} to {new_val} (Success)", (100, 255, 100))
                                            else:
                                                log(f"[HOTKEY] Failed to toggle {fname}: {msg}", (255, 100, 100))
                                    except Exception as e:
                                        log(f"[HOTKEY] Error during toggle {fname}: {e}", (255, 100, 100))
            
            if updated_flags:
                self.save_user_flags(skip_sync=True)
                self.last_apply_time = time.time()
                
            if triggered_this_cycle:
                last_success_trigger_time = time.time()
                # Brief sleep to prevent double-trigger from same press
                time.sleep(0.1)

    # ================================================================

    # ================================================================
    # Hybrid Flag Application (JSON + Memory)
    # ================================================================

    def apply_flags_hybrid(self, roblox_manager, skip_json=False):
        # (v4.0.5: frontend visibility heartbeat + Apply refusal removed — it
        # produced false-positives on narrow windows / network variance / ad-
        # network no-fill. Real settings-file signature check still runs where
        # it always did, inside Config.verify_settings_integrity.)
        # Watchdog stands down for the whole apply (reset in finally below) so it
        # can't write memory concurrently with us. Paired with the RobloxManager
        # memory lock, this removes the preset-switch write race entirely.
        self._applying = True
        try:
            with self._lock:
                flags_snapshot = list(self.user_flags)
            fps_unlock = _fps_unlock_on()

            if not flags_snapshot:
                log("[-] No flags to apply", (255, 200, 100))
                return

            total = len(flags_snapshot)

            # === Step 1: ClientAppSettings.json (always works) ===
            # Skipped for Scheduled Apply (B2): writing the JSON would let
            # Roblox read the flags at startup, defeating the requested delay.
            if not skip_json:
                log(f"[*] Writing {total} flags to ClientAppSettings.json...", (100, 255, 255))

                flags_dict = {}
                fps_skipped = 0
                for flag in flags_snapshot:
                    if not flag.get('enabled', True):
                        continue

                    name = flag['name']
                    if _skip_fps(name, fps_unlock):
                        fps_skipped += 1
                        continue  # FPS handled by the file-based FramerateCap unlock
                    val_str = str(flag['value'])
                    ftype = flag.get('type', 'string')

                    if ftype == 'bool':
                        val = val_str.lower() in ('true', '1', 'yes')
                    elif ftype == 'int':
                        try: val = int(val_str)
                        except ValueError: val = 0
                    elif ftype == 'float':
                        try: val = float(val_str)
                        except ValueError: val = 0.0
                    else:
                        val = val_str

                    flags_dict[name] = val

                # Explain the count gap users notice (e.g. 261 in the editor but
                # 260 applied): FPS flags are intentionally not written — the
                # file-based FramerateCap unlock handles FPS instead.
                if fps_skipped:
                    log(f"[·] {fps_skipped} FPS flag(s) skipped — handled by the "
                        f"FPS unlocker (count: {len(flags_dict)})", (150, 150, 150))

                json_ok, json_msg = roblox_manager.apply_fflags_json(flags_dict)

                if json_ok:
                    log(f"[+] JSON: {json_msg}", (100, 255, 100))
                    for flag in flags_snapshot:
                        # If the flag is disabled, it shouldn't show as "success" (green)
                        if not flag.get('enabled', True):
                            flag['_status'] = None
                        else:
                            flag['_status'] = 'success'
                else:
                    log(f"[-] JSON: {json_msg}", (255, 100, 100))
                    for flag in flags_snapshot:
                        if flag.get('enabled', True):
                            flag['_status'] = 'failed'
                        else:
                            flag['_status'] = None
            else:
                log(f"[*] Scheduled Apply: memory-only injection of {total} flags (JSON skipped)...", (100, 255, 255))

            # === Version-mismatch guard: skip live memory when offsets ≠ Roblox ===
            # The loaded offsets are dumped against SOME Roblox build (recorded
            # in offset_loader._last_source_build). If the ATTACHED Roblox PID
            # is running a DIFFERENT build, the RVAs from those offsets point
            # at wrong addresses — WriteProcessMemory hits random pages and
            # Roblox crashes on the next frame. The pre-2026-07 behaviour was
            # to log `[!] VERSION MISMATCH ... may fail or crash` and inject
            # anyway (explains the "crash after applying" reports in
            # CHANGELOG v4.0.2). The check now reads the attached PID's own
            # exe path (see _live_writes_gated) so it can't be fooled by a
            # bootstrapper writing a fresh version-YYY/ ahead of the running
            # PID.
            gated, reason = self._live_writes_gated(roblox_manager)
            if gated:
                log(f"[!] Live memory skipped — {reason}. JSON applied; live "
                    "flags will resume once offset sources catch up.",
                    (255, 200, 100))
                self.flags_applied = True
                self.last_apply_time = time.time()
                return

            # === Step 2: Live memory writes (only if Roblox is running) ===
            if not roblox_manager.is_attached:
                log("[*] Roblox not running — JSON applied, will take effect on next launch.", (255, 255, 100))
                self.flags_applied = True
                self.last_apply_time = time.time()
                return

            if not roblox_manager.open_process_for_write():
                log("[-] Could not open Roblox for memory writes. JSON was applied.", (255, 200, 100))
                self.flags_applied = True
                self.last_apply_time = time.time()
                return
                
            base = roblox_manager.get_roblox_base()
            if not base:
                log("[-] Could not resolve base address. JSON was applied.", (255, 200, 100))
                self.flags_applied = True
                self.last_apply_time = time.time()
                return

            # Live scan: find flag objects in the running process
            from src.utils.helpers import infer_type_from_name, clean_flag_name, infer_type
            target_names = []
            for f in flags_snapshot:
                fname = f['name']
                clean = clean_flag_name(fname)
                prefix = self.official_prefixes.get(clean)
                if prefix:
                    target_names.append(prefix + clean)
                else:
                    target_names.append(fname)
                    
            log("[*] Scanning live Roblox process for flag objects...", (100, 255, 255))
            # First Apply per PID does a full scan; subsequent Applies hit the
            # cache. If any target isn't covered (e.g. user added a new flag
            # since the last scan), force a rescan once to pick it up.
            live_addrs = roblox_manager.scan_live_flags(target_names, force_rescan=False)
            if live_addrs:
                missing_targets = {clean_flag_name(n) for n in target_names} - set(live_addrs.keys())
                if missing_targets:
                    live_addrs = roblox_manager.scan_live_flags(target_names, force_rescan=True)

            # Clear stale "unwritable" verdicts: a fresh scan may resolve a different
            # (possibly writable) address for the same flag if the build changed.
            for f in flags_snapshot:
                if '_unwritable' in f:
                    f.pop('_unwritable', None)

            mem_ok = 0
            mem_fail = 0
            mem_skip = 0
            mem_reverted = 0
            mem_json_only = 0
            enabled_flags = [f for f in flags_snapshot if f.get('enabled', True)]
            enabled_count = len(enabled_flags)
            total_list_count = len(flags_snapshot)
            _originals_captured = False

            for flag in flags_snapshot:
                name = flag['name']
                if _skip_fps(name, fps_unlock):
                    # FPS flags are NOT written (the file-based FramerateCap unlock
                    # handles FPS, and writing them fights it). Still show them as
                    # applied so the UI stays clean.
                    if flag.get('enabled', True):
                        flag['_status'] = 'success'
                    continue
                flag_type = infer_type_from_name(name) or flag.get('type', 'string')
                # The current offset dump stores flag names WITHOUT type prefixes,
                # so prefix inference yields 'unknown'. Fall back to the value the
                # user set (1000 -> int, true -> bool, 3.5 -> float) so the flag is
                # still applied to memory instead of being marked Unavailable.
                if flag_type == 'unknown':
                    flag_type = infer_type(str(flag.get('value', ''))) or 'unknown'
                is_enabled = flag.get('enabled', True)
                
                # String flags ARE written to live memory now: write_flag_at_address
                # handles the MSVC std::string layout (SSO inline vs heap repoint)
                # via _write_std_string, so they no longer fall back to JSON-only.

                # Skip unknown type flags — type could not be determined
                if flag_type == 'unknown':
                    mem_skip += 1
                    flag['_status'] = 'json_only' if flag.get('_status') == 'success' else 'unavailable'
                    continue
                    
                # (FPS flags are skipped at the top of this loop — the file-based
                # FramerateCap unlock handles FPS, so we never write TargetFps.)

                # Look up the live absolute address
                clean = clean_flag_name(name)
                addr_data = live_addrs.get(clean) or live_addrs.get(name)
                if not addr_data:
                    mem_skip += 1
                    flag['_status'] = 'json_only' if flag.get('_status') == 'success' else 'unavailable'
                    log(f"[·] SKIP: {name} (type={flag_type}) — no live address found", (180, 180, 180))
                    continue

                # addr_data is a list (legacy multi-address shape). Imtheo-only
                # produces a single entry per flag; iterate for shape stability.
                write_results = []
                for addr_entry in addr_data:
                    curr_abs_addr = addr_entry['abs_addr']
                    # Prefer user's explicitly provided type to support exploit overrides (e.g. NaN int for floats)
                    curr_live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
                    
                    # Capture the TRUE engine original before our first write so
                    # the kill switch / revert restores the real value (not the
                    # add-time default guess). Only capture once (original_captured
                    # marker, persisted) and only when the live value differs from
                    # what we're about to write — otherwise an already-applied flag
                    # would record its modified value as the "original".
                    if is_enabled and not flag.get('original_captured'):
                        orig_val = roblox_manager.read_flag_at_address(curr_live_type, curr_abs_addr)
                        if orig_val is not None and str(orig_val) != str(flag.get('value', '')):
                            flag['original_value'] = orig_val
                            flag['original_captured'] = True
                            _originals_captured = True

                    if is_enabled:
                        v_write = str(flag['value'])
                        res, msg = roblox_manager.write_flag_at_address(curr_live_type, curr_abs_addr, v_write)
                        write_results.append((res, msg, v_write))
                    else:
                        # Smart Reversion
                        if flag.get('_was_active', False) and 'original_value' in flag and flag['original_value'] is not None:
                            v_write = str(flag['original_value'])
                            res, msg = roblox_manager.write_flag_at_address(curr_live_type, curr_abs_addr, v_write)
                            write_results.append((res, msg, v_write))
                
                if not write_results:
                    mem_skip += 1
                    flag['_status'] = None
                    continue

                # Success if at least one write worked
                final_res = any(r[0] for r in write_results)
                success_msg = next((r[1] for r in write_results if r[0]), write_results[0][1])
                final_val = write_results[0][2]

                if final_res:
                    flag['_status'] = 'success'
                    if is_enabled:
                        mem_ok += 1
                        flag['_was_active'] = True
                        log(f"[+] MEM: {name} = {final_val} {success_msg}", (100, 255, 100))
                    else:
                        mem_reverted += 1
                        flag['_was_active'] = False
                        log(f"[+] MEM: Reversed {name} to {final_val} {success_msg}", (100, 255, 100))
                else:
                    if any(isinstance(r[1], str) and "JSON_ONLY" in r[1] for r in write_results):
                        mem_json_only += 1
                        flag['_status'] = 'json_only'
                        detail = next((r[1].split("|", 1)[1] for r in write_results
                                       if isinstance(r[1], str) and "JSON_ONLY" in r[1]), "")
                        log(f"[·] JSON-ONLY: {name} ({flag_type}) — {detail}", (180, 180, 255))
                    else:
                        mem_fail += 1
                        flag['_status'] = 'failed'
                        log(f"[-] MEM FAIL: {name} — {success_msg}", (255, 100, 100))

            if _originals_captured:
                self.save_user_flags()

            log(f"[=] Injection Result: {mem_ok}/{enabled_count} flags APPLIED via memory. "
                f"({mem_json_only} JSON-only, {mem_reverted} reverted, {mem_skip} skipped, {mem_fail} failed).",
                (100, 255, 100) if mem_fail == 0 else (200, 200, 100))
            
            if total_list_count > enabled_count:
                log(f"[·] Information: {total_list_count - enabled_count} flags in your list are currently DISABLED and were ignored.", (150, 150, 150))

            # Lock EVERY enabled flag with a live address (not just DF/S2). The
            # attribute byte at ptr-0x10 gates Roblox's config-reload path for any
            # flag type — locking a static flag is redundant but harmless (engine
            # never tries to re-populate it, so the bit is inert). External writes
            # (memory injection, hotkeys) still succeed regardless of lock state.
            if live_addrs:
                # Dedupe: a flag can appear under multiple names (clean + full),
                # and a shared address can end up in the list twice. The scanner
                # does set(target_addrs) internally, so the "Scheduling lock for
                # N…" count used to print a bigger N than the closing "Lock: X/M
                # covered…" line ever could. Dedupe here so both numbers agree.
                lock_targets_set = set()
                for _f in flags_snapshot:
                    if not _f.get('enabled', True):
                        continue
                    _n = _f['name']
                    _c = clean_flag_name(_n)
                    _entries = live_addrs.get(_c) or live_addrs.get(_n)
                    if _entries:
                        for _e in _entries:
                            _addr = _e.get('abs_addr')
                            if _addr:
                                lock_targets_set.add(_addr)
                if lock_targets_set:
                    lock_targets = list(lock_targets_set)
                    log(f"[*] Scheduling lock for {len(lock_targets)} flag address(es)...", (100, 200, 255))
                    threading.Thread(
                        target=roblox_manager.lock_dynamic_flags,
                        args=(lock_targets,),
                        daemon=True
                    ).start()

            # Start watchdog if we have dynamic flags
            self.start_watchdog(roblox_manager)

            self.flags_applied = True
            self.last_apply_time = time.time()
            # Number of enabled flags we attempted to apply this run. Used by the
            # caller to decide whether to play the apply sound (>=1 = something
            # actually applied; 0 = empty/all-disabled -> stay silent).
            return enabled_count
        except Exception as e:
            log(f"[-] CRITICAL ERROR in apply_flags_hybrid: {e}", (255, 50, 50))
            import traceback
            log(traceback.format_exc(), (255, 50, 50))
            return 0
        finally:
            # Apply finished (or failed) — let the watchdog resume enforcing.
            self._applying = False

    def launch_and_apply(self, roblox_manager):
        """Launch Roblox, then apply live memory flags. Writes ClientAppSettings.json
        first when there are enabled flags to persist.

        Zero-flag path is intentional: the button is 'Launch Roblox' — a fresh
        install with no flags configured must still be able to start the game.
        Skip the JSON write and the live-apply, but keep the launch and let the
        watchdog spin up so a later add-flag mid-game still enforces."""
        has_flags = bool(self.user_flags)
        total = len(self.user_flags)

        # === Step 1: Write JSON (only when we have flags to persist) ===
        if has_flags:
            log("[*] Writing active flags to ClientAppSettings.json...", (100, 255, 255))
            fps_unlock = _fps_unlock_on()
            flags_dict = {}
            for flag in self.user_flags:
                if not flag.get('enabled', True):
                    continue
                name = flag['name']
                if _skip_fps(name, fps_unlock):
                    continue  # FPS handled by the file-based FramerateCap unlock
                val_str = str(flag['value'])
                ftype = flag.get('type', 'string')

                if ftype == 'bool':
                    val = val_str.lower() in ('true', '1', 'yes')
                elif ftype == 'int':
                    try: val = int(val_str)
                    except ValueError: val = 0
                elif ftype == 'float':
                    try: val = float(val_str)
                    except ValueError: val = 0.0
                else:
                    val = val_str
                flags_dict[name] = val

            json_ok, json_msg = roblox_manager.apply_fflags_json(flags_dict)
            if json_ok:
                log(f"[+] JSON: {json_msg}", (100, 255, 100))
            else:
                log(f"[-] JSON: {json_msg}", (255, 100, 100))

        # === Step 2: Launch and (if flags exist) apply live ===
        if has_flags:
            log(f"[*] Launching Roblox to apply active flags...", (100, 255, 255))
        else:
            log("[*] Launching Roblox (no flags configured — nothing to inject)...", (100, 255, 255))

        success, pid, _, _ = roblox_manager.launch_and_patch_roblox(self.user_flags)
        
        if success:
            if has_flags:
                log(f"[+] Roblox launched (PID {pid}), waiting for initialization...", (100, 255, 100))

                # Wait for Roblox to initialize its memory. Also track whether the
                # process actually stays alive, so a failed apply can tell "Roblox
                # never started" apart from "running but we couldn't read it".
                initialized = False
                saw_process = False
                for i in range(15):
                    time.sleep(1.0)
                    if roblox_manager.is_roblox_running():
                        saw_process = True
                    elif not saw_process and i >= 3:
                        # ~4s in and no Roblox process ever showed up — it didn't start.
                        break
                    roblox_manager.attach()
                    if roblox_manager.is_attached and roblox_manager.get_roblox_base():
                        # Check if memory is readable yet
                        if roblox_manager.read_memory_external(roblox_manager.get_roblox_base(), 100):
                            initialized = True
                            break

                if initialized:
                    log(f"[+] Process initialized, applying live memory flags...", (100, 255, 100))
                    time.sleep(1.0)  # Give it a moment to allocate flag objects
                    self.apply_flags_hybrid(roblox_manager)
                elif not roblox_manager.is_roblox_running():
                    # CreateProcess succeeded but Roblox closed right after launch.
                    log("[-] Roblox did not start — it closed right after launch.", (255, 100, 100))
                    log("    Likely: your Roblox build is out of date (try 'Fix Roblox'), the install is broken/half-updated, or antivirus blocked it. Your flags are saved to JSON.", (255, 180, 100))
                    for flag in self.user_flags:
                        flag['_status'] = 'json_only'
                else:
                    # Process is alive but its memory stayed unreadable.
                    log("[-] Roblox is running, but FFM couldn't read its memory to apply live flags.", (255, 100, 100))
                    log("    Try running FFM as administrator. Your flags are in JSON and will apply on the next clean launch.", (255, 180, 100))
                    for flag in self.user_flags:
                        if flag.get('_status') != 'success' and flag.get('type', 'string') != 'string':
                            flag['_status'] = 'json_only'
            else:
                # Zero-flag launch: nothing to inject or wait for. Just confirm the
                # spawn — the watchdog below still starts so flags added mid-game
                # apply live.
                log(f"[+] Roblox launched (PID {pid})", (100, 255, 100))
        else:
            log("[-] Launch failed — Roblox could not be started.", (255, 200, 100))
            if has_flags:
                log("    Flags are saved to JSON; restart Roblox manually to apply them.", (255, 200, 100))
                for flag in self.user_flags:
                    flag['_status'] = 'json_only'

        # Start watchdog to maintain DF flags (safe no-op with an empty flag list).
        self.start_watchdog(roblox_manager)

