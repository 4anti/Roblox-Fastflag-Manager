import json
import re
import os
import time
import threading
from src.utils.config import Config
from src.utils.logger import log
from src.utils.helpers import infer_type, clean_flag_name

class FlagManager:
    def __init__(self):
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
        self._watchdog_thread = None
        self._hotkey_thread = None
        self._rm = None
        self.hotkeys_inhibited = False
        
        self.load_user_flags()

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
                
            flags_dict = {}
            for flag in flags_snapshot:
                if not flag.get('enabled', True):
                    continue
                    
                name = flag['name']
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
        if limit < 0: return  # Negative means disabled completely
        
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
            with self._lock:
                for f in self.user_flags:
                    f['_status'] = None
                    official = self.official_types.get(f['name'])
                    if official and official != 'unknown':
                        f['type'] = official

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
        
        while self._watchdog_running:
            # Reload settings periodically (every 60s) so changes take effect without restart
            now = time.time()
            if now - last_settings_reload > 60.0:
                settings = Config.load_settings()
                interval = settings.get("watchdog_interval", 5.0)
                enforce_all = settings.get("enforce_all_flags", True)
                if last_settings_reload == 0:
                    log(f"[*] Watchdog loop active (Interval: {interval}s, EnforceAll: {enforce_all})", (150, 150, 255))
                last_settings_reload = now
            
            time.sleep(interval)
            
            if not self.user_flags or not self._rm or not self._rm.is_attached:
                continue
                
            # Filter flags for enforcement
            if enforce_all:
                enforce_list = [f for f in self.user_flags if f.get('enabled', True) and f.get('type', 'string') != 'string']
            else:
                enforce_list = [f for f in self.user_flags if str(f.get('name', '')).startswith('DF') and f.get('enabled', True)]
            
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

                    success, msg = self._rm.write_flag_at_address(live_type, abs_addr, str(value))
                    write_results.append((success, msg))
                
                if any(r[0] for r in write_results):
                    reapplied += 1
                elif all(isinstance(r[1], str) and (r[1].startswith("JSON_ONLY") or r[1].startswith("STALE_ADDR")) for r in write_results):
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
        
        while self._hotkey_running:
            time.sleep(0.05)
            
            # Global Inhibition Check (e.g. Bind Picker or Menu is open)
            with self._lock:
                if self.hotkeys_inhibited:
                    continue
            
            # 1. Identify all keys we need to monitor
            vks_to_check = set()
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
            
            with self._lock:
                for flag in self.user_flags:
                    bind = flag.get('bind')
                    unapply_bind = flag.get('unapply_bind')
                    fname = flag['name']
                    flag_type = flag.get('type', 'string')
                    
                    # Un-apply action
                    if unapply_bind and VK_MAP.get(unapply_bind) in just_pressed:
                        if flag.get('enabled', True):
                            flag['enabled'] = False
                            updated_flags = True
                            triggered_this_cycle = True
                            log(f"[HOTKEY] Un-applied {fname}", (255, 150, 150))
                            if 'original_value' in flag:
                                addr_data = self._rm.get_live_flag_address(fname)
                                if addr_data:
                                    try:
                                        self._rm.open_process_for_write()
                                        for addr_entry in addr_data:
                                            abs_addr = addr_entry['abs_addr']
                                            live_type = flag_type if flag_type != 'unknown' else addr_entry.get('type', 'unknown')
                                            self._rm.write_flag_at_address(live_type, abs_addr, str(flag['original_value']))
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
                            if self._rm:
                                log(f"[HOTKEY] TaskSchedulerTargetFps -> {new_val}", (100, 255, 255))
                                self._rm.write_fps_direct(int(new_val))
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
                                if addr_data:
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

    def apply_flags_hybrid(self, roblox_manager):
        try:
            with self._lock:
                flags_snapshot = list(self.user_flags)
                
            if not flags_snapshot:
                log("[-] No flags to apply", (255, 200, 100))
                return

            total = len(flags_snapshot)
            
            # === Step 1: ClientAppSettings.json (always works) ===
            log(f"[*] Writing {total} flags to ClientAppSettings.json...", (100, 255, 255))
            
            flags_dict = {}
            for flag in flags_snapshot:
                if not flag.get('enabled', True):
                    continue
                    
                name = flag['name']
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
            from src.utils.helpers import infer_type_from_name, clean_flag_name
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
                flag_type = infer_type_from_name(name) or flag.get('type', 'string')
                is_enabled = flag.get('enabled', True)
                
                # Skip string flags — can't safely write to std::string in memory
                if flag_type == 'string':
                    mem_skip += 1
                    flag['_status'] = 'json_only' if flag.get('_status') == 'success' else 'unavailable'
                    continue
                
                # Skip unknown type flags — type could not be determined
                if flag_type == 'unknown':
                    mem_skip += 1
                    flag['_status'] = 'json_only' if flag.get('_status') == 'success' else 'unavailable'
                    continue
                    
                # Special fast-path for TaskSchedulerTargetFps (FPS unlocked)
                clean = clean_flag_name(name)
                
                if clean == "TaskSchedulerTargetFps":
                    if is_enabled:
                        value_to_write = str(flag['value'])
                        success, message = roblox_manager.write_fps_direct(int(float(value_to_write)))
                        if success:
                            flag['_status'] = 'success'
                            mem_ok += 1
                            log(f"[+] MEM: {name} = {value_to_write} (DirectPattern)", (100, 255, 100))
                            continue
                        else:
                            mem_fail += 1
                            flag['_status'] = 'failed'
                            log(f"[-] MEM FAIL: TaskSchedulerTargetFps — {message}", (255, 100, 100))
                            continue

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
                    
                    # Capture original value before first modification
                    if is_enabled and 'original_value' not in flag:
                        orig_val = roblox_manager.read_flag_at_address(curr_live_type, curr_abs_addr)
                        if orig_val is not None:
                            flag['original_value'] = orig_val
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
                        log(f"[·] JSON-ONLY: {name}", (180, 180, 255))
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
                    
            # Start watchdog if we have dynamic flags
            self.start_watchdog(roblox_manager)
            
            self.flags_applied = True
            self.last_apply_time = time.time()
        except Exception as e:
            log(f"[-] CRITICAL ERROR in apply_flags_hybrid: {e}", (255, 50, 50))
            import traceback
            log(traceback.format_exc(), (255, 50, 50))

    def launch_and_apply(self, roblox_manager):
        """Write JSON first, then launch Roblox and apply live memory flags."""
        if not self.user_flags:
            log("[-] No flags to apply", (255, 200, 100))
            return
        
        total = len(self.user_flags)
        
        # === Step 1: Write JSON (always) ===
        log(f"[*] Writing active flags to ClientAppSettings.json...", (100, 255, 255))
        flags_dict = {}
        for flag in self.user_flags:
            if not flag.get('enabled', True):
                continue
            name = flag['name']
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
        
        # === Step 2: Launch and apply live ===
        log(f"[*] Launching Roblox to apply active flags...", (100, 255, 255))
        
        success, pid, _, _ = roblox_manager.launch_and_patch_roblox(self.user_flags)
        
        if success:
            log(f"[+] Roblox launched (PID {pid}), waiting for initialization...", (100, 255, 100))
            
            # Wait for Roblox to initialize its memory
            initialized = False
            for _ in range(15):
                time.sleep(1.0)
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
            else:
                log(f"[-] Could not attach to Roblox after launch", (255, 100, 100))
                for flag in self.user_flags:
                    if flag.get('_status') != 'success' and flag.get('type', 'string') != 'string':
                        flag['_status'] = 'json_only'
        else:
            log(f"[-] Launch failed — flags are in JSON, restart Roblox manually", (255, 200, 100))
            for flag in self.user_flags:
                flag['_status'] = 'json_only'
                
        # Start watchdog to maintain DF flags
        self.start_watchdog(roblox_manager)

