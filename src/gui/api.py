import os
import json
import time
import threading
import ctypes
from ctypes import wintypes
import webview
from src.utils.updater import check_for_updates, perform_silent_update, get_current_version, apply_staged_update, download_update
from src.utils.logger import log, get_logs
from src.utils.config import Config
from src.utils.helpers import infer_type, infer_type_from_name, clean_flag_name, get_flag_prefix, get_default_value
from src.core.roblox_manager import RobloxManager
from src.core.flag_manager import FlagManager
from src.core.preset_manager import PresetManager

# Win32 Constants
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND = 0x0112
HTCAPTION = 2
SC_SIZE = 0xF000


class Api:
    def set_hotkeys_inhibited(self, inhibited):
        if hasattr(self, 'flag_manager') and self.flag_manager:
            self.flag_manager.set_hotkeys_inhibited(inhibited)

    def __init__(self):
        self._window = None  # Set after window creation
        self._last_apply_time = 0
        self._init_error = None
        self.processed_pids = set()
        self.update_ready = False
        self._pending_update = None  # {version, exe_url, changelog}
        self._update_progress = 0  # 0-100 for frontend polling
        self._last_offsets_loaded_state = False

        # Initialize subsystems with error recovery — UI must always load
        try:
            self.roblox_manager = RobloxManager()
        except Exception as e:
            self.roblox_manager = None
            self._init_error = f"RobloxManager init failed: {e}"
            log(f"[!] {self._init_error}", (255, 100, 100))

        try:
            self.flag_manager = FlagManager()
        except Exception as e:
            self.flag_manager = None
            self._init_error = f"FlagManager init failed: {e}"
            log(f"[!] {self._init_error}", (255, 100, 100))

        try:
            self.preset_manager = PresetManager()
        except Exception as e:
            self.preset_manager = None
            log(f"[!] PresetManager init failed: {e}", (255, 100, 100))

        try:
            self.settings = Config.load_settings()
            # Default history limit: 30
            if 'history_limit' not in self.settings:
                self.settings['history_limit'] = 30
            # Default UI theme: premium
            if 'ui_theme' not in self.settings:
                self.settings['ui_theme'] = 'premium'
        except Exception:
            self.settings = {'auto_apply': False, 'history_limit': 30, 'ui_theme': 'premium'}

        # Load offsets in background thread (not on main thread!)
        threading.Thread(target=self._init_offsets, daemon=True).start()

        # Pre-emptive Sync on Startup: Ensure ClientAppSettings.json is ready for browser launches
        if self.flag_manager and self.settings.get('auto_apply', False):
            threading.Thread(target=self.flag_manager.sync_json_to_roblox, args=(self.roblox_manager,), daemon=True).start()

        # Start background monitor thread
        if self.flag_manager:
            self.flag_manager.start_hotkey_listener(self.roblox_manager)
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _update_loop(self):
        """Background thread: Check for updates periodically."""
        while True:
            try:
                has_update, exe_url, remote_version, changelog = check_for_updates()
                if has_update:
                    if exe_url:
                        if self.settings.get('auto_update', False):
                            # Auto mode: download and install silently
                            if perform_silent_update(exe_url, remote_version):
                                self.update_ready = True
                        else:
                            # Manual mode: store update info for the UI
                            self._pending_update = {
                                'version': remote_version,
                                'exe_url': exe_url,
                                'changelog': changelog or ''
                            }
                            log(f"[*] Update v{remote_version} available. Check Settings to install.", (100, 255, 100))
                    else:
                        log(f"[*] Update v{remote_version} is available on GitHub, but the Installer (.exe) is missing from the release assets.", (255, 200, 100))
            except Exception as e:
                log(f"[!] Background update loop error: {e}", (255, 100, 100))
            
            # Sleep for 10 minutes
            time.sleep(600)

    def _init_offsets(self):
        """Background thread: load flag offsets without blocking UI."""
        try:
            if self.flag_manager:
                log("[*] Loading flag offsets...", (100, 255, 255))
                self.flag_manager.load_offsets()
        except Exception as e:
            log(f"[!] Offset loading failed: {e}", (255, 100, 100))

    def get_loading_status(self):
        """Return loading state for the frontend."""
        if not self.flag_manager:
            return {'ready': False, 'error': self._init_error or 'FlagManager not available'}
        return {
            'ready': self.flag_manager.offsets_loaded,
            'loading': self.flag_manager.offsets_loading,
            'count': len(self.flag_manager.preset_flags_list),
            'error': self._init_error,
            'update_ready': getattr(self, 'update_ready', False),
            'pending_update': True if getattr(self, '_pending_update', None) else False,
            'version': get_current_version(),
        }

    # ─── Settings ───

    def get_settings(self):
        return {
            'auto_apply': self.settings.get('auto_apply', False),
            'theme': self.settings.get('theme', 'dark'),
            'close_to_tray': self.settings.get('close_to_tray', False),
            'launch_minimized': self.settings.get('launch_minimized', False),
            'history_limit': self.settings.get('history_limit', 30),
            'ui_theme': self.settings.get('ui_theme', 'premium'),
            'sidebar_width': self.settings.get('sidebar_width', 240),
            'console_height': self.settings.get('console_height', 180),
            'sidebar_collapsed': self.settings.get('sidebar_collapsed', False),
            'sort_mode': self.settings.get('sort_mode', 'custom'),
            'auto_update': self.settings.get('auto_update', False),
        }

    def set_history_limit(self, value):
        try:
            val = int(value)
            self.settings['history_limit'] = val
            Config.save_settings(self.settings)
            log(f"[+] History limit set to: {'Unlimited' if val >= 100 else val}")
        except Exception:
            pass

    def set_auto_apply(self, value):
        self.settings['auto_apply'] = value
        Config.save_settings(self.settings)
        log(f"[+] Auto Apply: {'ON' if value else 'OFF'}")

    def set_theme(self, theme):
        self.settings['theme'] = theme
        Config.save_settings(self.settings)

    def set_ui_theme(self, theme):
        self.settings['ui_theme'] = theme
        Config.save_settings(self.settings)

    def set_close_to_tray(self, value):
        self.settings['close_to_tray'] = value
        Config.save_settings(self.settings)
        log(f"[+] Close to tray: {'ON' if value else 'OFF'}")

    def set_launch_minimized(self, value):
        self.settings['launch_minimized'] = value
        Config.save_settings(self.settings)
        log(f"[+] Launch minimized: {'ON' if value else 'OFF'}")

    def set_sort_mode(self, mode):
        self.settings['sort_mode'] = mode
        Config.save_settings(self.settings)
        log(f"[+] Sort Mode: {mode}")

    def set_auto_update(self, value):
        self.settings['auto_update'] = value
        Config.save_settings(self.settings)
        log(f"[+] Auto Update: {'ON' if value else 'OFF'}")

    def get_update_info(self):
        """Return pending update info for the frontend."""
        if self._pending_update:
            return {
                'available': True,
                'version': self._pending_update['version'],
                'changelog': self._pending_update['changelog'],
                'current': get_current_version()
            }
        return {
            'available': False,
            'current': get_current_version()
        }

    def get_update_progress(self):
        """Return download progress (0-100) for the frontend overlay."""
        return self._update_progress

    def trigger_manual_update(self):
        """User clicked 'Update Now'. Download with progress in a background thread."""
        if not self._pending_update:
            return False

        def do_download():
            info = self._pending_update
            def on_progress(downloaded, total):
                self._update_progress = int((downloaded / total) * 100)

            success = download_update(info['exe_url'], info['version'], progress_callback=on_progress)
            if success:
                self._update_progress = 100
                time.sleep(1.0)  # Brief pause to let the .bat process register before we exit
                os._exit(0)
            else:
                self._update_progress = -1  # Signal failure

        self._update_progress = 0
        threading.Thread(target=do_download, daemon=True).start()
        return True

    def open_url(self, url):
        """Open a URL in the default system browser."""
        import webbrowser
        try:
            webbrowser.open(url)
            log(f"[*] Opening URL: {url}")
        except Exception as e:
            log(f"[!] Failed to open URL: {e}", (255, 100, 100))

    # ─── Available Flags ───
    
    def _refresh_search_cache(self, search_term):
        """Unified method to refresh the search cache."""
        search_lower = search_term.lower()
        
        # If term hasn't changed, don't re-calculate
        if hasattr(self, '_search_cache_term') and self._search_cache_term == search_lower:
            return
            
        combined_list = self.flag_manager.preset_flags_list
        
        if not search_lower:
            self._search_cache = combined_list
        else:
            # Fuzzy search: match search term against either the FULL name or the CLEANED name
            from src.utils.helpers import clean_flag_name
            self._search_cache = [
                name for name in combined_list 
                if search_lower in name.lower() or search_lower in clean_flag_name(name).lower()
            ]
            
        self._search_cache_term = search_lower

    def get_fflag_count(self, search='') -> int:
        """Get total number of discovered flags, optionally filtered by search."""
        if not self.flag_manager:
            return 0
            
        self._refresh_search_cache(search)
        return len(self._search_cache)

    def get_available_flags(self, search='', offset=0, limit=300):
        """Return filtered list of available flags with pagination from cache."""
        if not self.flag_manager:
            return []
            
        search_lower = search.lower()
        
        self._refresh_search_cache(search)
        
        source_list = self._search_cache
        user_flags_dict = {f['name']: f.get('type', 'unknown') for f in self.flag_manager.user_flags}
        
        results = []
        # Slice the cache for the requested range
        chunk = source_list[offset : offset + limit]
        
        for name in chunk:
            # Priority: 1. Official Scanner Type, 2. Prefix Guess, 3. Value Guess (from added list)
            expected = self.flag_manager.official_types.get(name) or \
                       infer_type_from_name(name) or \
                       user_flags_dict.get(name) or \
                       'unknown'

            prefix = get_flag_prefix(name)
            results.append({
                'name': name,
                'added': name in user_flags_dict,
                'expected_type': expected,
                'prefix': prefix
            })
        return results

    # ─── User Flags ───

    def get_user_flags(self):
        """Return list of user's configured flags."""
        if not self.flag_manager:
            return []

        preset_set = set(self.flag_manager.preset_flags_list)
        # Pre-calculate clean names for faster lookup
        clean_presets = {clean_flag_name(p): p for p in self.flag_manager.preset_flags_list}

        return [{
            'name': f['name'],
            'display_name': clean_flag_name(f['name']),
            'value': str(f.get('value', '')),
            'type': f.get('type', 'string'),
            'status': f.get('_status', None),
            'is_unrecognized': f['name'] not in preset_set 
                               and clean_flag_name(f['name']) not in clean_presets,
            'is_known': f['name'] in preset_set or clean_flag_name(f['name']) in clean_presets,
            'enabled': f.get('enabled', True),            'bind': f.get('bind', ''),
            'unapply_bind': f.get('unapply_bind', ''),
            'cycle_states': f.get('cycle_states', []),
            'prefix': self.flag_manager.official_prefixes.get(f['name'], '') or get_flag_prefix(f['name'])
        } for f in self.flag_manager.user_flags]

    def validate_flag_value(self, name, value):
        """Validate a value against the expected type from the flag's Roblox prefix."""
        expected = infer_type_from_name(name)
        if not expected or expected == 'string':
            return True, None  # Strings accept everything
            
        val_str = str(value).strip().lower()
        
        if expected == 'bool':
            if val_str not in ('true', 'false'):
                return False, f"\u274c {name} is a BOOL flag \u2014 value must be 'true' or 'false', got '{value}'"
            return True, None
            
        if expected == 'int':
            try:
                int(val_str)
                return True, None
            except ValueError:
                return False, f"\u274c {name} is an INT flag — value must be a whole number, got '{value}'"
        
        return True, None

    def add_flag(self, name, value):
        """Add a flag to user configuration with type validation."""
        if not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
        
        # We store the name EXACTLY as provided to preserve prefixes required for JSON/Memory.
        # Duplicate checking is done using normalized (cleaned) names.
        clean_new = clean_flag_name(name)
        with self.flag_manager._lock:
            if any(clean_flag_name(f['name']) == clean_new for f in self.flag_manager.user_flags):
                log(f"[-] Flag already added: {name}", (255, 176, 32))
                return {'ok': False, 'error': f'{name} (or a variant) is already in your configuration'}
        
        # Validate value against expected type (uses full name if possible)
        ok, err = self.validate_flag_value(name, value)
        if not ok:
            log(f"[-] {err}", (255, 100, 100))
            return {'ok': False, 'error': err}
            
        # Priority: 1. Official Scanner Type, 2. Prefix Guess, 3. Value Guess
        flag_type = self.flag_manager.official_types.get(name) or \
                    infer_type_from_name(name) or \
                    infer_type(value)
        self.flag_manager.save_history_snapshot(f"Before adding {name}", self.settings.get('history_limit', 30))
        
        new_flag = {
            'name': name,
            'value': str(value),
            'type': flag_type,
            'enabled': True
        }
        
        # Proactive Original Value Capture
        if self.roblox_manager and self.roblox_manager.is_attached:
            addr_data = self.roblox_manager.get_live_flag_address(name)
            if addr_data:
                orig = self.roblox_manager.read_flag_at_address(flag_type, addr_data['abs_addr'])
                if orig is not None:
                    new_flag['original_value'] = orig
                    log(f"[*] Captured original value for {name}: {orig}")
        
        if 'original_value' not in new_flag:
            new_flag['original_value'] = get_default_value(name)

        with self.flag_manager._lock:
            self.flag_manager.user_flags.append(new_flag)
            
        self.flag_manager.save_user_flags()
        log(f"[+] Added {name} (type: {flag_type})")
        if self.settings.get('auto_apply'): self.inject()
        return {'ok': True}

    def batch_add_flags(self, flags_list):
        """Add multiple flags at once. flags_list: [{'name': '...', 'value': '...'}, ...]"""
        if not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
        
        self.flag_manager.save_history_snapshot(f"Before batch add ({len(flags_list)} flags)", self.settings.get('history_limit', 30))
        
        added = 0
        skipped = 0
        errors = []
        
        with self.flag_manager._lock:
            for item in flags_list:
                name = item.get('name')
                val = item.get('value')
                if not name or val is None:
                    continue
                
                clean_new = clean_flag_name(name)
                # Check for duplicates using cleaned names
                if any(clean_flag_name(f['name']) == clean_new for f in self.flag_manager.user_flags):
                    skipped += 1
                    continue
                
                # Validate value
                ok, err = self.validate_flag_value(name, val)
                if not ok:
                    errors.append(f"{name}: {err}")
                    continue
                
                flag_type = infer_type_from_name(name) or infer_type(str(val))
                self.flag_manager.user_flags.append({
                    'name': name,
                    'value': str(val),
                    'type': flag_type,
                    'enabled': True,
                    'original_value': get_default_value(name)
                })
                added += 1
                
        self.flag_manager.save_user_flags()
        log(f"[+] Batch Import: {added} added, {skipped} skipped, {len(errors)} errors")
        if self.settings.get('auto_apply') and added > 0: self.inject()
        return {'ok': True, 'added': added, 'skipped': skipped, 'errors': errors}

    def set_flag_bind(self, name, key):
        """Set a hotkey bind for a specific flag."""
        if not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
        
        target = None
        for flag in self.flag_manager.user_flags:
            if flag['name'] == name:
                target = flag
                break
                
        if not target:
            return {'ok': False, 'error': f'{name} not found in configuration'}
            
        if key:
            target['bind'] = key
            log(f"[+] Bound {name} to {key}")
        else:
            if 'bind' in target:
                del target['bind']
            log(f"[-] Removed bind for {name}")
            
        self.flag_manager.save_user_flags()
        return {'ok': True}

    def set_advanced_bind(self, name, data):
        """Set advanced bind data (cycle_states, unapply_bind) for a flag."""
        if not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
            
        target = None
        for flag in self.flag_manager.user_flags:
            if flag['name'] == name:
                target = flag
                break
                
        if not target:
            return {'ok': False, 'error': f'{name} not found'}
            
        if 'unapply_bind' in data:
            val = data['unapply_bind']
            if val:
                target['unapply_bind'] = val
                log(f"[+] Set un-apply bind for {name}: {val}")
            elif 'unapply_bind' in target:
                del target['unapply_bind']
                log(f"[-] Removed un-apply bind for {name}")
                
        if 'cycle_states' in data:
            target['cycle_states'] = data['cycle_states']
            
        self.flag_manager.save_user_flags()
        return {'ok': True}

    def update_flag(self, name, value):
        """Update a flag's value with type validation."""
        if not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
        
        # Find the flag and check its stored type for validation
        target = None
        for flag in self.flag_manager.user_flags:
            if flag['name'] == name:
                target = flag
                break
        if not target:
            return {'ok': False, 'error': f'{name} not found'}
        
        # Reconstruct full name for prefix-based validation
        full_name = None
        if self.flag_manager.preset_flags_list:
            for preset in self.flag_manager.preset_flags_list:
                if clean_flag_name(preset) == name:
                    full_name = preset
                    break
        
        if full_name:
            # Validate using the full prefixed name
            ok, err = self.validate_flag_value(full_name, value)
            if not ok:
                log(f"[-] {err}", (255, 100, 100))
                return {'ok': False, 'error': err}
        else:
            # Fallback: validate using the stored type directly
            stored_type = target.get('type', 'string')
            val_str = str(value).strip().lower()
            if stored_type == 'bool' and val_str not in ('true', 'false'):
                err = f"\u274c {name} is a BOOL flag \u2014 value must be 'true' or 'false', got '{value}'"
                log(f"[-] {err}", (255, 100, 100))
                return {'ok': False, 'error': err}
            if stored_type == 'int':
                try:
                    int(val_str)
                except ValueError:
                    err = f"\u274c {name} is an INT flag \u2014 value must be a whole number, got '{value}'"
                    log(f"[-] {err}", (255, 100, 100))
                    return {'ok': False, 'error': err}
        
        self.flag_manager.save_history_snapshot(f"Before updating {name}", self.settings.get('history_limit', 30))
        
        with self.flag_manager._lock:
            # Find the flag again under lock
            target = None
            for flag in self.flag_manager.user_flags:
                if flag['name'] == name:
                    target = flag
                    break
            
            if not target:
                return {'ok': False, 'error': f'{name} not found'}
                
            target['value'] = value
            # Keep the original prefix-derived type, don't re-guess
            
        self.flag_manager.save_user_flags()
        log(f"[+] Updated {name}")
        if self.settings.get('auto_apply'): self.inject()
        return {'ok': True}

    def remove_flags(self, names):
        """Remove flags by name."""
        if not self.flag_manager:
            return
        count = len(names)
        self.flag_manager.save_history_snapshot(f"Before removing {count} flag(s)", self.settings.get('history_limit', 30))
        
        with self.flag_manager._lock:
            self.flag_manager.user_flags = [
                f for f in self.flag_manager.user_flags if f['name'] not in names
            ]
            
        self.flag_manager.save_user_flags()
        log(f"[+] Removed {count} flag(s)")
        if self.settings.get('auto_apply'): self.inject()

    def get_flag_type_info(self, name):
        """Return the expected type of a flag based on its Roblox prefix."""
        expected = infer_type_from_name(name)
        prefix = get_flag_prefix(name)
        return {
            'expected_type': expected or 'unknown',
            'prefix': prefix or '?',
            'hint': {
                'bool': 'true or false',
                'int': 'whole number (e.g. 0, 60, 9999)',
                'string': 'text value',
            }.get(expected, 'any value')
        }

    def clear_all(self):
        """Clear all user flags."""
        if not self.flag_manager:
            return
        self.flag_manager.save_history_snapshot("Before clear all", self.settings.get('history_limit', 30))
        with self.flag_manager._lock:
            self.flag_manager.user_flags.clear()
        self.flag_manager.save_user_flags()
        log("[+] Cleared all flags")
        if self.settings.get('auto_apply'): self.inject()
        
    def get_history(self):
        if not self.flag_manager: return []
        return self.flag_manager.get_history()

    def clear_history(self):
        if not self.flag_manager: return False
        return self.flag_manager.clear_history()
        
    def restore_history(self, timestamp):
        if not self.flag_manager: return False
        try:
            ts = int(timestamp)
            success = self.flag_manager.restore_history(ts)
            if success and self.settings.get('auto_apply'):
                self.inject()
            return success
        except Exception:
            return False

    def toggle_flag_apply(self, name):
        """Toggle the enabled state of a specific flag."""
        if not self.flag_manager: return False
        
        with self.flag_manager._lock:
            target = None
            for flag in self.flag_manager.user_flags:
                if flag['name'] == name:
                    target = flag
                    break
            
            if not target: return False
            
            is_enabled = target.get('enabled', True)
            target['enabled'] = not is_enabled
            new_state = target['enabled']
            
            # Clear internal status immediately if disabling
            if new_state == False:
                target['_status'] = None
                
        self.flag_manager.save_user_flags()
        log(f"[*] {name} is now {'ENABLED' if new_state else 'DISABLED'}")
            
        # Only trigger re-injection if Roblox is attached
        if self.settings.get('auto_apply') and self.roblox_manager and self.roblox_manager.is_attached:
            self.inject()
        return True

    def reorder_flags(self, names_list):
        """Reorder user_flags based on the provided list of names."""
        if not self.flag_manager or not names_list: return
        
        with self.flag_manager._lock:
            current_flags = {f['name']: f for f in self.flag_manager.user_flags}
            new_list = []
            
            for name in names_list:
                if name in current_flags:
                    new_list.append(current_flags[name])
                    del current_flags[name]
            
            # Add any remaining flags that weren't in the list
            new_list.extend(current_flags.values())
            
            self.flag_manager.user_flags = new_list
            
        self.flag_manager.save_user_flags()
        log("[+] Custom flag order updated")

    # ─── Actions ───

    def inject(self):
        """Apply flags using hybrid method (JSON + live memory)."""
        if not self.flag_manager or not self.roblox_manager:
            log("[-] Not ready", (255, 100, 100))
            return
        if getattr(self, '_is_applying', False):
            log("[-] Busy applying flags, please wait...", (255, 200, 100))
            return
            
        self._is_applying = True
        def do_inject():
            try:
                # Try to attach (not required — JSON works without Roblox running)
                self.roblox_manager.attach()
                log("[*] Applying flags (hybrid: JSON + live memory)...", (100, 255, 255))
                self.flag_manager.apply_flags_hybrid(self.roblox_manager)
            except Exception as e:
                log(f"[-] CRITICAL CRASH in apply logic: {e}", (255, 50, 50))
                import traceback
                traceback.print_exc()
            finally:
                self._is_applying = False
                if self._window:
                    self._window.evaluate_js("""
                        var btn = document.getElementById('inject-btn');
                        if (btn) {
                            btn.disabled = false;
                            btn.textContent = 'Apply Flags';
                        }
                        if (typeof refreshConfig === 'function') refreshConfig();
                    """)
                    
        import threading
        threading.Thread(target=do_inject, daemon=True).start()

    def launch_and_apply(self):
        """Launch Roblox suspended, patch ALL flags before Hyperion, then resume."""
        if not self.flag_manager or not self.roblox_manager:
            log("[-] Not ready", (255, 100, 100))
            return
        if getattr(self, '_is_applying', False):
            log("[-] Busy applying flags, please wait...", (255, 200, 100))
            return
            
        self._is_applying = True
        def do_launch():
            try:
                log("[*] Launch & Apply: JSON + early patching...", (100, 255, 255))
                self.flag_manager.launch_and_apply(self.roblox_manager)
            except Exception as e:
                log(f"[-] CRITICAL CRASH in launch logic: {e}", (255, 50, 50))
                import traceback
                traceback.print_exc()
            finally:
                self._is_applying = False
                if self._window:
                    self._window.evaluate_js("""
                        var btn = document.getElementById('inject-btn');
                        if (btn) {
                            btn.disabled = false;
                            btn.textContent = 'Apply Flags';
                        }
                        if (typeof refreshConfig === 'function') refreshConfig();
                    """)
                    
        threading.Thread(target=do_launch, daemon=True).start()

    def reapply_flags(self):
        """Kill Roblox, then relaunch with all flags pre-patched (mid-game reapply)."""
        if not self.flag_manager or not self.roblox_manager:
            log("[-] Not ready", (255, 100, 100))
            return
        if getattr(self, '_is_applying', False):
            log("[-] Busy applying flags, please wait...", (255, 200, 100))
            return
            
        self._is_applying = True
        def do_reapply():
            try:
                # Kill existing Roblox
                killed = self.roblox_manager.kill_roblox()
                if killed > 0:
                    log(f"[*] Killed {killed} Roblox process(es)", (255, 200, 100))
                    # Clear processed PIDs so auto-apply doesn't interfere
                    self.processed_pids.clear()
                    # Wait for process to fully die
                    import time
                    time.sleep(1.5)
                else:
                    log("[*] No Roblox process found, launching fresh...", (100, 255, 255))
                
                # Now launch with early patching
                log("[*] Relaunching with all flags pre-patched...", (100, 255, 255))
                self.flag_manager.launch_and_apply(self.roblox_manager)
            except Exception as e:
                log(f"[-] CRASH in reapply: {e}", (255, 50, 50))
                import traceback
                traceback.print_exc()
            finally:
                self._is_applying = False
                if self._window:
                    self._window.evaluate_js("""
                        var btn = document.getElementById('inject-btn');
                        if (btn) {
                            btn.disabled = false;
                            btn.textContent = 'Apply Flags';
                        }
                        if (typeof refreshConfig === 'function') refreshConfig();
                    """)
                    
        threading.Thread(target=do_reapply, daemon=True).start()

    def import_flags(self):
        """Import flags from JSON file. Supports:
        - Bloxstrap-style: {"FFlagName": value, "FIntName": 123, ...}
        - FFlagManager list: [{"name": "...", "value": "...", "type": "..."}, ...]
        """
        if not self._window or not self.flag_manager:
            return False
        try:
            result = self._window.create_file_dialog(
                dialog_type=10,  # OPEN_DIALOG
                file_types=('JSON Files (*.json)',),
            )
            if result and len(result) > 0:
                file_path = result[0]
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Support both Bloxstrap-style dicts {"FFlagName": Value} and FFlagManager list
                if isinstance(data, dict):
                    items_to_process = [{'name': k, 'value': v} for k, v in data.items()]
                elif isinstance(data, list):
                    items_to_process = data
                else:
                    items_to_process = []
                    
                self.flag_manager.save_history_snapshot("Before import", self.settings.get('history_limit', 30))    
                added = 0
                skipped = 0
                for item in items_to_process:
                    name = item.get('name')
                    val = item.get('value')
                    if name and val is not None:
                        # DO NOT clean the name; Roblox JSON and memory patching require the prefix
                        if any(f['name'] == name for f in self.flag_manager.user_flags):
                            skipped += 1
                            continue
                        # Use prefix-based type detection, fall back to value guessing
                        flag_type = infer_type_from_name(name) or infer_type(str(val))
                        self.flag_manager.user_flags.append({
                            'name': name, 'value': str(val), 'type': flag_type,
                            'enabled': True,
                            'original_value': get_default_value(name)
                        })
                        added += 1
                self.flag_manager.save_user_flags()
                log(f"[+] Imported {added} flags ({skipped} duplicates skipped)")
                if self.settings.get('auto_apply') and added > 0: self.inject()
                return True
        except Exception as e:
            log(f"[-] Import error: {e}", (255, 85, 85))
        return False

    def export_flags(self):
        """Export flags to JSON file."""
        if not self._window or not self.flag_manager:
            log("[-] Not ready", (255, 85, 85))
            return False
            
        with self.flag_manager._lock:
            if not self.flag_manager.user_flags:
                log("[-] No flags to export", (255, 85, 85))
                return False
            
        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename='flags.json',
                file_types=('JSON Files (*.json)',),
            )
            if result:
                file_path = result if isinstance(result, str) else result[0]
                
                export_data = []
                with self.flag_manager._lock:
                    for f in self.flag_manager.user_flags:
                        # Include flags and their binds, but omit internal runtime fields like _status
                        flag_data = {
                            'name': f['name'],
                            'value': f.get('value', ''),
                            'type': f.get('type', 'string'),
                            'enabled': f.get('enabled', True),
                        }
                        if 'bind' in f: flag_data['bind'] = f['bind']
                        if 'unapply_bind' in f: flag_data['unapply_bind'] = f['unapply_bind']
                        if 'cycle_states' in f: flag_data['cycle_states'] = f['cycle_states']
                        export_data.append(flag_data)
                        
                with open(file_path, 'w', encoding='utf-8') as fp:
                    json.dump(export_data, fp, indent=4)
                log(f"[+] Exported {len(export_data)} flags to {os.path.basename(file_path)}")
                return True
        except Exception as e:
            log(f"[-] Export error: {e}", (255, 85, 85))
        return False

    def export_preset_base64(self, name):
        if not self.preset_manager: return None
        for p in self.preset_manager.get_presets():
            if p.get('name') == name:
                import base64
                import zlib
                j = json.dumps(p)
                return base64.b64encode(zlib.compress(j.encode('utf-8'))).decode('utf-8')
        return None

    def export_preset_json(self, name):
        if not self.preset_manager: return None
        for p in self.preset_manager.get_presets():
            if p.get('name') == name:
                return json.dumps(p, indent=4)
        return None

    def import_preset_clipboard(self, raw_string):
        if not self.preset_manager: return False, "Manager not ready"
        try:
            data = None
            if raw_string.strip().startswith('{') or raw_string.strip().startswith('['):
                try:
                    data = json.loads(raw_string)
                except Exception:
                    pass
            
            if not data:
                try:
                    import base64
                    import zlib
                    j = zlib.decompress(base64.b64decode(raw_string.strip())).decode('utf-8')
                    data = json.loads(j)
                except Exception:
                    return False, "Invalid Base64 or JSON format"

            if not data:
                return False, "Could not parse data"

            flags = []
            if isinstance(data, list):
                flags = data
                name = 'Imported Preset'
            elif isinstance(data, dict):
                if 'name' in data and 'flags' in data:
                    name = data['name'] + ' (Imported)'
                    # Phase 1: Auto-Correct types in complex presets
                    flags = []
                    for f in data['flags']:
                        nf = dict(f)
                        if 'name' in nf:
                            nf['type'] = infer_type_from_name(nf['name']) or nf.get('type', 'string')
                        flags.append(nf)
                else:
                    flags = [{'name': k, 'value': str(v), 'type': infer_type_from_name(k) or infer_type(v)} for k, v in data.items()]
                    name = 'Imported Preset'

            if flags:
                new_preset = self.preset_manager.import_preset_from_file_data(name, flags)
                log(f"[+] Imported preset '{name}' from clipboard with {len(flags)} flags")
                return True, new_preset
            return False, "No valid flags found"
        except Exception as e:
            return False, str(e)

    def trigger_updater_restart(self):
        try:
            apply_staged_update()
            if self._window: self._window.destroy()
            import sys
            sys.exit(0)
        except Exception as e:
            log(f"[-] Restart failed: {e}", (255, 100, 100))

    def panic_revert(self):
        if not self.flag_manager: return False
        try:
            log("[!] PANIC REVERT INITIATED! Disabling all custom flags.", (255, 50, 50))
            with self.flag_manager._lock:
                for f in self.flag_manager.user_flags:
                    f['enabled'] = False
            self.flag_manager.save_user_flags()
            self.flag_manager.apply_flags_hybrid(self.roblox_manager)
            return True
        except Exception as e:
            log(f"[-] Panic revert failed: {e}", (255, 50, 50))
            return False

    def rescan_offsets(self):
        """Force a deep memory scan to refresh all FFlag memory locations."""
        if not self.roblox_manager:
            return
            
        try:
            log("[*] Manually triggering deep memory scan...", (100, 255, 255))
            
            # Nuclear Reset: Delete local vault and flush memory cache
            from src.utils.config import Config
            if Config.KNOWN_FLAGS_FILE.exists():
                try:
                    Config.KNOWN_FLAGS_FILE.unlink()
                    log("[*] Local Vault file deleted.", (180, 180, 180))
                except Exception as e:
                    log(f"[!] Failed to delete vault file: {e}", (255, 150, 150))
            
            self.roblox_manager.clear_bank_cache()
            
            # Clear preset list (CDN data) to ensure a blank slate
            if self.flag_manager:
                self.flag_manager.official_types = {}
                self.flag_manager.official_prefixes = {}
                self.flag_manager.preset_flags_list = []
                # Clear search UI cache
                if hasattr(self, '_search_cache_term'):
                    del self._search_cache_term
                log("[*] Official CDN cache cleared.", (180, 180, 180))
            
            if not self.roblox_manager.is_attached:
                self.roblox_manager.attach()
            if not self.roblox_manager.is_attached:
                log("[-] Scan Failed: Roblox is not running.", (255, 100, 100))
                return
            # Build target list from user flags for the rescan
            target_names = []
            if self.flag_manager:
                from src.utils.helpers import clean_flag_name, get_flag_prefix
                for f in self.flag_manager.user_flags:
                    fname = f['name']
                    clean = clean_flag_name(fname)
                    prefix = self.flag_manager.official_prefixes.get(clean)
                    target_names.append(prefix + clean if prefix else fname)
            live_addrs = self.roblox_manager.scan_live_flags(target_names or None, force_rescan=True)
            if live_addrs:
                log(f"[+] Scan Complete: Found {len(live_addrs)} FFlag locations.", (100, 255, 100))
                # Force search UI to refresh with new vault data
                if hasattr(self, '_search_cache_term'):
                    del self._search_cache_term
            else:
                log("[-] Scan Failed: No flag addresses found.", (255, 100, 100))
        except Exception as e:
            log(f"[-] Scan Error: {e}", (255, 100, 100))
            import traceback
            traceback.print_exc()

    # ─── Presets ───

    def get_presets(self):
        if not self.preset_manager: return []
        return self.preset_manager.get_presets()

    def import_preset_from_file(self):
        if not self._window or not self.preset_manager:
            return {'ok': False, 'error': 'Not ready'}
        try:
            result = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=('JSON Files (*.json)',))
            if result and len(result) > 0:
                file_path = result[0]
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Check format
                flags = []
                if isinstance(data, list):
                    # Phase 1: Auto-Correct types in list imports
                    flags = []
                    for f in data:
                        if isinstance(f, dict) and 'name' in f:
                            nf = dict(f)
                            nf['type'] = infer_type_from_name(nf['name']) or nf.get('type', 'string')
                            flags.append(nf)
                        else:
                            flags.append(f)
                elif isinstance(data, dict):
                    flags = [{'name': k, 'value': str(v), 'type': infer_type_from_name(k) or infer_type(v)} for k, v in data.items()]
                
                if flags:
                    filename = os.path.basename(file_path).replace('.json', '')
                    new_preset = self.preset_manager.import_preset_from_file_data(filename, flags)
                    log(f"[+] Imported preset '{filename}' with {len(flags)} flags")
                    return {'ok': True, 'preset': new_preset}
                return {'ok': False, 'error': 'No flags found in file'}
            return {'ok': False, 'error': 'Cancelled'}
        except Exception as e:
            log(f"[-] Preset import error: {e}", (255, 85, 85))
            return {'ok': False, 'error': str(e)}

    def import_preset_from_config(self, name, color):
        if not self.preset_manager or not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
        
        # Strip internal fields like _status, but keep binds
        clean_flags = []
        with self.flag_manager._lock:
            for f in self.flag_manager.user_flags:
                flag_data = {
                    'name': f['name'],
                    'value': f.get('value', ''),
                    'type': f.get('type', 'string'),
                    'enabled': f.get('enabled', True),
                }
                if 'bind' in f: flag_data['bind'] = f['bind']
                if 'unapply_bind' in f: flag_data['unapply_bind'] = f['unapply_bind']
                if 'cycle_states' in f: flag_data['cycle_states'] = f['cycle_states']
                clean_flags.append(flag_data)
        
        if not clean_flags:
            return {'ok': False, 'error': 'Current configuration is empty'}

        new_preset = self.preset_manager.add_preset(name, clean_flags, color)
        log(f"[+] Saved current configuration as preset '{name}'")
        return {'ok': True, 'preset': new_preset}

    def update_preset_from_config(self, preset_id):
        if not self.preset_manager or not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}

        clean_flags = []
        with self.flag_manager._lock:
            for f in self.flag_manager.user_flags:
                flag_data = {
                    'name': f['name'],
                    'value': f.get('value', ''),
                    'type': f.get('type', 'string'),
                    'enabled': f.get('enabled', True),
                }
                if 'bind' in f: flag_data['bind'] = f['bind']
                if 'unapply_bind' in f: flag_data['unapply_bind'] = f['unapply_bind']
                if 'cycle_states' in f: flag_data['cycle_states'] = f['cycle_states']
                clean_flags.append(flag_data)
        
        if not clean_flags:
            return {'ok': False, 'error': 'Current configuration is empty'}

        success = self.preset_manager.update_preset(preset_id, flags=clean_flags)
        if success:
            presets = self.preset_manager.get_presets()
            name = next((p['name'] for p in presets if p['id'] == preset_id), 'Unknown')
            log(f"[+] Updated preset '{name}' flags with current configuration")
            return {'ok': True}
        return {'ok': False, 'error': 'Preset not found'}

    def merge_preset(self, preset_id):
        if not self.preset_manager or not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
        
        presets = self.preset_manager.get_presets()
        preset = next((p for p in presets if p['id'] == preset_id), None)
        if not preset:
            return {'ok': False, 'error': 'Preset not found'}
        
        incoming_flags = preset['flags']
        new_count = 0
        updated_count = 0
        
        with self.flag_manager._lock:
            # Create a map of current flags for faster lookup
            current_map = {f['name']: f for f in self.flag_manager.user_flags}
            
            for incoming in incoming_flags:
                name = incoming['name']
                if name in current_map:
                    current = current_map[name]
                    # Logic: If incoming has a bind but current DOES NOT, we accept the incoming one (to get the bind)
                    # If current already has a bind, we keep current (ignore incoming).
                    has_current_bind = current.get('bind') or current.get('unapply_bind')
                    has_incoming_bind = incoming.get('bind') or incoming.get('unapply_bind')
                    
                    if has_incoming_bind and not has_current_bind:
                        # Update current with incoming data
                        current['value'] = incoming['value']
                        current['type'] = incoming.get('type', 'string')
                        if 'bind' in incoming: current['bind'] = incoming['bind']
                        if 'unapply_bind' in incoming: current['unapply_bind'] = incoming['unapply_bind']
                        if 'cycle_states' in incoming: current['cycle_states'] = incoming['cycle_states']
                        updated_count += 1
                else:
                    # New flag, just add it
                    self.flag_manager.user_flags.append(incoming.copy())
                    new_count += 1
            
            self.flag_manager.save_user_flags()
            
        log(f"[+] Merged preset '{preset['name']}': {new_count} new, {updated_count} updated with binds")
        return {'ok': True}

    def apply_preset(self, preset_id):
        if not self.preset_manager or not self.flag_manager:
            return {'ok': False, 'error': 'Not ready'}
            
        presets = self.preset_manager.get_presets()
        target = next((p for p in presets if p["id"] == preset_id), None)
        if not target:
            return {'ok': False, 'error': 'Preset not found'}
            
        self.flag_manager.save_history_snapshot(f"Before applying preset '{target['name']}'", self.settings.get('history_limit', 30))
        
        # We need to map the preset flags over. Ensure they have 'enabled': True set
        new_user_flags = []
        for pf in target['flags']:
            nf = dict(pf)
            # Phase 2: Refresh types during application (Source of truth: Name)
            nf['type'] = infer_type_from_name(nf['name']) or nf.get('type', 'string')
            if 'enabled' not in nf:
                nf['enabled'] = True
            new_user_flags.append(nf)
            
        with self.flag_manager._lock:
            self.flag_manager.user_flags = new_user_flags
            
        self.flag_manager.save_user_flags()
        
        log(f"[+] Applied preset '{target['name']}' ({len(new_user_flags)} flags)")
        if self.settings.get('auto_apply'): self.inject()
        return {'ok': True}

    def update_preset(self, preset_id, name, color):
        if not self.preset_manager: return False
        success = self.preset_manager.update_preset(preset_id, name, color)
        if success: log(f"[*] Updated preset {name}")
        return success

    def delete_preset(self, preset_id):
        if not self.preset_manager: return False
        success = self.preset_manager.delete_preset(preset_id)
        if success: log(f"[-] Deleted preset")
        return success

    def reorder_presets(self, ids):
        if not self.preset_manager: return False
        return self.preset_manager.reorder_presets(ids)

    # ─── Status ───

    def get_status(self):
        """Return current connection status."""
        fm = self.flag_manager
        rm = self.roblox_manager
        needs_refresh = False
        if fm:
            # Check for scanner completion (removes startup question marks)
            if fm.offsets_loaded and not self._last_offsets_loaded_state:
                self._last_offsets_loaded_state = True
                needs_refresh = True
                log("[*] Scanner finished, updating UI with recognized flags", (100, 255, 100))

            # Check for manual application
            if fm.last_apply_time > self._last_apply_time:
                self._last_apply_time = fm.last_apply_time
                needs_refresh = True
        return {
            'attached': bool(rm and rm.is_attached),
            'pid': (rm.pid or 0) if rm else 0,
            'flag_count': len(fm.user_flags) if fm else 0,
            'needs_refresh': needs_refresh,
        }

    # ─── Logs ───

    def get_logs(self, since_index=0):
        """Return new log entries since the given index."""
        logs = get_logs()
        new_logs = logs[since_index:]
        return {
            'logs': [{'msg': msg, 'color': list(color) if color else None} 
                     for msg, color in new_logs],
            'total': len(logs)
        }

    # ─── Window Controls ───

    def _get_hwnd(self):
        """Helper to find the true top-level HWND for the WebView2 window."""
        if not self._window or not self._window.native_id:
            return None
        # On Windows pywebview native_id is already the HWND
        hwnd = self._window.native_id
        # Safety: Ensure we have the top-level window
        parent = ctypes.windll.user32.GetAncestor(hwnd, 2) # GA_ROOT
        return parent if parent else hwnd

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def toggle_maximize(self):
        """Toggle between maximized and normal window state."""
        if self._window:
            try:
                is_max = getattr(self, '_maximized', False)
                if not is_max:
                    self._window.maximize()
                    self._maximized = True
                else:
                    self._window.restore()
                    self._maximized = False
                
                # Save state change
                self.settings['window_maximized'] = self._maximized
                Config.save_settings(self.settings)
                
                return self._maximized
            except Exception as e:
                log(f"[!] Maximize error: {e}", (255, 100, 100))
        return False

    def start_drag(self):
        """Invoke native Win32 window dragging."""
        hwnd = self._get_hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.ReleaseCapture()
                # 0x0112 = WM_SYSCOMMAND, 0xF012 = SC_MOVE + 2 (Drag)
                ctypes.windll.user32.PostMessageW(hwnd, 0x0112, 0xF012, 0)
            except Exception as e:
                log(f"[!] Drag error: {e}", (255, 100, 100))

    def start_resize(self, direction):
        """Invoke native Win32 window resizing."""
        hwnd = self._get_hwnd()
        if hwnd:
            try:
                # 0x0112 = WM_SYSCOMMAND, 0xF000 = SC_SIZE
                # SC_SIZE + direction (1=L, 2=R, 3=T, 4=TL, 5=TR, 6=B, 7=BL, 8=BR)
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.PostMessageW(hwnd, 0x0112, 0xF000 | direction, 0)
            except Exception as e:
                log(f"[!] Resize error: {e}", (255, 100, 100))

    def get_window_bounds(self):
        """Return the current window bounds for JS-based resizing fallback."""
        if self._window:
            try:
                # pywebview usually has width and height attributes
                return {
                    'width': self._window.width,
                    'height': self._window.height,
                    'x': getattr(self._window, 'x', 0),
                    'y': getattr(self._window, 'y', 0)
                }
            except Exception:
                pass
        return {'width': 1050, 'height': 780, 'x': 0, 'y': 0}

    def resize_window(self, width, height):
        """Directly resize the window from the frontend."""
        if self._window:
            try:
                # Ensure we don't go below min_size
                w = max(800, int(width))
                h = max(600, int(height))
                self._window.resize(w, h)
            except Exception:
                pass

    def save_ui_layout(self, layout):
        """Save the UI layout (sidebar, console) to settings."""
        if not layout:
            return
        
        try:
            self.settings['sidebar_width'] = layout.get('sidebarWidth', 240)
            self.settings['console_height'] = layout.get('consoleHeight', 180)
            self.settings['sidebar_collapsed'] = layout.get('isSidebarCollapsed', False)
            # We don't save to disk on every resize to keep it snappy.
            # State will be persisted on exit/move.
        except Exception as e:
            log(f"[!] Error updating UI layout state: {e}", (255, 100, 100))

    def save_window_state(self):
        """Save the current window geometry to settings."""
        if not self._window:
            return
        
        try:
            # Update maximized status
            is_max = getattr(self, '_maximized', False)
            self.settings['window_maximized'] = is_max
            
            # ONLY save dimensions if NOT maximized
            if not is_max:
                self.settings['window_width'] = self._window.width
                self.settings['window_height'] = self._window.height
            
            Config.save_settings(self.settings)
        except Exception as e:
            log(f"[!] Error saving window state: {e}", (255, 100, 100))

    def close_window(self):
        """Handle window close request from UI."""
        if self._window:
            # Check the setting from our loaded config
            if self.settings.get('close_to_tray', False):
                # Save state before hiding
                self.save_window_state()
                # Hide to tray via MainWindow instead of destroying
                if hasattr(self, '_app'):
                    self._app.hide_window()
                else:
                    self._window.hide()
                log("[*] Application hidden to system tray", (180, 180, 200))
            else:
                self.exit_app()

    def exit_app(self):
        """Full application exit (from UI or Tray)."""
        log("[*] Closing application...", (255, 100, 100))
        self.save_window_state()
        if hasattr(self, '_app'):
            self._app.exit_app()
        elif self._window:
            self._window.destroy()

    # ─── Background Monitor ───

    def _monitor_loop(self):
        """Background thread: monitor Roblox process (Auto Apply)."""
        while True:
            try:
                if not self.roblox_manager or not self.flag_manager:
                    time.sleep(5)
                    continue

                # We call find_roblox_process manually to avoid unwanted side effects of attach() if we aren't ready
                pid = self.roblox_manager.find_roblox_process()
                
                if pid:
                    # If Auto Apply is on, and this is a new pid we haven't processed
                    if self.settings.get('auto_apply') and pid not in self.processed_pids and self.flag_manager.offsets_loaded:
                        log(f"[*] Auto Apply: New Roblox detected (PID {pid}), applying flags...", (100, 255, 255))
                        self.processed_pids.add(pid)
                        # We must attach first so inject() knows it's ready
                        if self.roblox_manager.attach():
                            self.inject()
                    else:
                        # Just attach to update status
                        self.roblox_manager.attach()
                else:
                    self.roblox_manager.reset()
                    self.flag_manager.flags_applied = False
                    # Clear statuses
                    with self.flag_manager._lock:
                        for f in self.flag_manager.user_flags:
                            if f.get('_status'):
                                f['_status'] = None
                    # Clean up old PIDs to prevent unbounded growth
                    self.processed_pids.clear()
            except Exception as e:
                log(f"[!] Monitor error: {e}", (255, 100, 100))
                time.sleep(5)  # Back off on error
                continue
            time.sleep(2)
