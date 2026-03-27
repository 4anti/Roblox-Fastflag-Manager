import json
import re
import os
import time
import threading
from src.utils.config import Config
from src.utils.logger import log
from src.utils.helpers import infer_type, clean_flag_name
from src.utils.updater import update_fflags

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
        
        self.load_user_flags()

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

    def save_user_flags(self):
        try:
            with self._lock:
                clean_flags = []
                for f in self.user_flags:
                    clean_flags.append({k: v for k, v in f.items() if not k.startswith('_')})
                    
            with open(Config.USER_FLAGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(clean_flags, f, indent=4)
            return True
        except Exception as e:
            log(f"Failed to save flags: {e}", (255, 100, 100))
            return False

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
        except:
            return []

    def clear_history(self):
        """Clear all history snapshots."""
        try:
            with open(Config.HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=4)
            return True
        except:
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

    def load_offsets(self):
        """Load flag offsets natively using the offline scanner. Safe to call from background thread."""
        if self.offsets_loading:
            return
        self.offsets_loading = True

        try:
            import src.core.roblox_manager as r_man
            # The manager's static method will spawn a suspended process and scan it automatically
            offsets = r_man.RobloxManager.fetch_offsets()
            if offsets:
                self.all_offsets = {name: int(offset, 16) if isinstance(offset, str) else offset for name, offset in offsets.items()}
                self.preset_flags_list = sorted(list(self.all_offsets.keys()))
                self.offsets_loaded = True
                
                # Fetch official types seamlessly in background
                threading.Thread(target=self._fetch_official_types, daemon=True).start()
                
        except Exception as e:
            log(f"Failed to load FFlags natively: {e}", (255, 100, 100))
        finally:
            self.offsets_loading = False

    def _fetch_official_types(self):
        """Fetch the official ClientSettings to resolve exact types for unadded flags."""
        try:
            import urllib.request
            import json
            from src.utils.helpers import infer_type_from_name, clean_flag_name, get_flag_prefix
            url = "https://clientsettingscdn.roblox.com/v1/settings/application?applicationName=PCDesktopClient"
            req = urllib.request.Request(url, headers={'User-Agent': 'Roblox/WinInet'})
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    settings = data.get('applicationSettings', {})
                    for full_name in settings.keys():
                        unprefixed = clean_flag_name(full_name)
                        ftype = infer_type_from_name(full_name)
                        prefix = get_flag_prefix(full_name)
                        if ftype:
                            self.official_types[unprefixed] = ftype
                        if prefix:
                            self.official_prefixes[unprefixed] = prefix
            log(f"[+] Loaded {len(self.official_types)} official flag types from CDN.")
        except Exception as e:
            log(f"[-] Failed to fetch official flag types: {e}", (255, 100, 100))

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
        settings = Config.load_settings()
        interval = settings.get("watchdog_interval", 5.0)
        enforce_all = settings.get("enforce_all_flags", True)
        
        log(f"[*] Watchdog loop active (Interval: {interval}s, EnforceAll: {enforce_all})", (150, 150, 255))
        
        while self._watchdog_running:
            time.sleep(interval)
            
            if not self.user_flags or not self._rm or not self._rm.is_attached:
                continue
                
            # Filter flags for enforcement
            if enforce_all:
                # All enabled memory-writable flags
                enforce_list = [f for f in self.user_flags if f.get('enabled', True) and f.get('type', 'string') != 'string']
            else:
                # Only DF prefixed flags (legacy behavior)
                enforce_list = [f for f in self.user_flags if str(f.get('name', '')).startswith('DF') and f.get('enabled', True)]
            
            if not enforce_list:
                continue
                
            # Need to re-open for write if closed
            if not self._rm.open_process_for_write():
                continue
                
            reapplied = 0
            for flag in enforce_list:
                name = flag['name']
                value = flag['value']
                flag_type = flag.get('type', 'string')
                
                offset_hex = self._rm.get_offset_for_flag(name)
                if not offset_hex:
                    continue
                    
                try: offset_int = int(offset_hex, 16)
                except ValueError: continue
                
                # Write current value to memory
                success, _ = self._rm.write_flag_external(name, flag_type, offset_int, str(value))
                if success:
                    reapplied += 1
                    
            if reapplied > 0:
                # Only log if it's been a while since the last log to avoid spamming
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
            'Insert': 0x2D, 'Delete': 0x2E, 'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
            'MouseLeft': 0x01, 'MouseRight': 0x02, 'MouseMiddle': 0x04, 'MouseX1': 0x05, 'MouseX2': 0x06
        }
        key_states = {}
        last_bind_error_time = 0
        last_success_trigger_time = 0
        
        while self._hotkey_running:
            time.sleep(0.05)
            
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
                key_states[vk] = is_p
            
            if not just_pressed:
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
                                offset_hex = self._rm.get_offset_for_flag(fname)
                                if offset_hex:
                                    try:
                                        self._rm.open_process_for_write()
                                        self._rm.write_flag_external(fname, flag_type, int(offset_hex, 16), str(flag['original_value']))
                                    except: pass

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
                                offset_hex = self._rm.get_offset_for_flag(fname)
                                if offset_hex:
                                    try:
                                        offset_int = int(offset_hex, 16)
                                        if 'original_value' not in flag:
                                            orig_val = self._rm.read_flag_external(flag_type, offset_int)
                                            if orig_val is not None: flag['original_value'] = orig_val
                                        self._rm.open_process_for_write()
                                        self._rm.write_flag_external(fname, flag_type, offset_int, new_val)
                                        log(f"[HOTKEY] Toggled {fname} to {new_val}", (255,100,255))
                                    except: pass
            
            if updated_flags:
                self.save_user_flags()
                self.last_apply_time = time.time()
                
            if triggered_this_cycle:
                last_success_trigger_time = time.time()

    # ================================================================

    # ================================================================
    # Hybrid Flag Application (JSON + Memory)
    # ================================================================

    def apply_flags_hybrid(self, roblox_manager):
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

        mem_ok = 0
        mem_fail = 0
        mem_skip = 0
        mem_reverted = 0
        enabled_flags = [f for f in flags_snapshot if f.get('enabled', True)]
        enabled_count = len(enabled_flags)
        total_list_count = len(flags_snapshot)
        failed_flags = []  # Flags that fail external writes

        for flag in flags_snapshot:
            name = flag['name']
            flag_type = flag.get('type', 'string')
            is_enabled = flag.get('enabled', True)
            
            # Skip string flags — can't write to fixed-size memory
            if flag_type == 'string':
                mem_skip += 1
                flag['_status'] = 'unavailable'
                # log(f"[-] MEM: Skipping {name} (String flags require restart)", (200, 200, 100))
                continue

            # Look up RVA offset
            offset_hex = roblox_manager.get_offset_for_flag(name)
            if not offset_hex:
                mem_skip += 1
                flag['_status'] = 'unavailable'
                continue

            try:
                offset_int = int(offset_hex, 16)
            except Exception:
                mem_skip += 1
                continue

            # Capture original value before we modify memory for the first time
            if is_enabled and 'original_value' not in flag:
                orig_val = roblox_manager.read_flag_external(flag_type, offset_int)
                if orig_val is not None:
                    flag['original_value'] = orig_val
                    self.save_user_flags()

            if is_enabled:
                value_to_write = str(flag['value'])
                flag['_was_active'] = True
            else:
                # Smart Reversion: Only write if we have an original AND we were previously active
                if flag.get('_was_active', False) and 'original_value' in flag and flag['original_value'] is not None:
                    value_to_write = str(flag['original_value'])
                else:
                    mem_skip += 1
                    flag['_status'] = None # Clean up status if it's inactive and never touched
                    log(f"[-] MEM: Skipping {name} (Disabled, no original value or not previously active)", (200, 200, 100))
                    continue

            success, message = roblox_manager.write_flag_external(name, flag_type, offset_int, value_to_write)

            if success:
                flag['_status'] = 'success'
                if is_enabled:
                    mem_ok += 1
                    log(f"[+] MEM: {name} = {value_to_write} {message}", (100, 255, 100))
                else:
                    mem_reverted += 1
                    flag['_was_active'] = False # RESET ONLY ON SUCCESSFUL REVERSION
                    log(f"[+] MEM: Reversed {name} to {value_to_write} {message}", (100, 255, 100))
            else:
                mem_fail += 1
                flag['_status'] = 'unavailable'
                if is_enabled:
                    failed_flags.append(flag)

        # The count logic: X/Y where X is successful enabled flags, Y is total enabled flags
        log(f"[=] Injection Result: {mem_ok}/{enabled_count} flags APPLIED. ({mem_reverted} reverted, {mem_skip} skipped).", 
            (100, 255, 100) if mem_fail == 0 else (200, 200, 100))
        
        if total_list_count > enabled_count:
            log(f"[·] Information: {total_list_count - enabled_count} flags in your list are currently DISABLED and were ignored.", (150, 150, 150))
                
        # Start watchdog if we have dynamic flags
        self.start_watchdog(roblox_manager)
        
        self.flags_applied = True
        self.last_apply_time = time.time()

    def launch_and_apply(self, roblox_manager):
        """Write JSON first, then launch Roblox suspended and patch ALL flags early.
        
        This bypasses Hyperion's SEC_NO_CHANGE locks by patching before init.
        """
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
        
        # === Step 2: Launch suspended + early patch ===
        log(f"[*] EARLY PATCH: Launching Roblox suspended, patching active flags...", (100, 255, 255))
        
        # Pass ALL flags so we capture original values even for disabled ones
        success, patched, attempted, new_pid = roblox_manager.launch_and_patch_roblox(self.user_flags)
        
        if success:
            log(f"[+] Launch & Apply complete: {patched}/{attempted} flags patched early (PID {new_pid})", (100, 255, 100))
            # Mark all enabled flags as "was active" for smart reversion later
            for f in self.user_flags:
                if f.get('enabled', True):
                    f['_was_active'] = True
            
            self.flags_applied = True
            self.last_apply_time = time.time()
            # Persist captured original values
            self.save_user_flags()
            for flag in self.user_flags:
                if flag.get('_status') != 'success' and flag.get('type', 'string') != 'string':
                    flag['_status'] = 'json_only'
        else:
            log(f"[-] Early patch failed — flags are in JSON, restart Roblox manually", (255, 200, 100))
            for flag in self.user_flags:
                flag['_status'] = 'json_only'
                
        # Start watchdog to maintain DF flags
        self.start_watchdog(roblox_manager)
        
        self.flags_applied = True
        self.last_apply_time = time.time()

