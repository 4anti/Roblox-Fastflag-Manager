import os
import sys
import time
import struct
import subprocess
import threading
import ctypes
import ctypes.wintypes as wintypes
import json
import urllib.request
from src.utils.logger import log
import src.core.scanner as scanner

# ================================================================
# ctypes function prototypes — MUST be defined before first call
# to prevent 64-bit pointer truncation (handles are pointer-sized)
# ================================================================
_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_ntdll = ctypes.WinDLL('ntdll', use_last_error=True)

# Process management
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE

_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL

_k32.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
_k32.TerminateProcess.restype = wintypes.BOOL

# Toolhelp snapshots
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Process32FirstW.restype = wintypes.BOOL

_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Process32NextW.restype = wintypes.BOOL

_k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Module32FirstW.restype = wintypes.BOOL

_k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Module32NextW.restype = wintypes.BOOL

# Memory operations — critical for 64-bit correctness
_k32.VirtualProtectEx.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
]
_k32.VirtualProtectEx.restype = wintypes.BOOL

_k32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
_k32.WriteProcessMemory.restype = wintypes.BOOL

_k32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
_k32.ReadProcessMemory.restype = wintypes.BOOL

# NT syscalls
_ntdll.NtWriteVirtualMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
_ntdll.NtWriteVirtualMemory.restype = ctypes.c_long  # NTSTATUS

_ntdll.NtReadVirtualMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
_ntdll.NtReadVirtualMemory.restype = ctypes.c_long

# Process creation (for CREATE_SUSPENDED)
_k32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.c_void_p, ctypes.c_void_p
]
_k32.CreateProcessW.restype = wintypes.BOOL

_k32.ResumeThread.argtypes = [wintypes.HANDLE]
_k32.ResumeThread.restype = wintypes.DWORD

# NtQueryInformationProcess — get PEB address for base resolution
_ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p,
    ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)
]
_ntdll.NtQueryInformationProcess.restype = ctypes.c_long

# Memory query
class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]

_k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t

# ================================================================
# Offsets Caching
# ================================================================
_cached_offsets = None

# ================================================================
# Windows structures
# ================================================================
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]

class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_wchar * 256),
        ("szExePath", ctypes.c_wchar * 260),
    ]

# Process access rights
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_ACCESS = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION

PAGE_READWRITE = 0x04
CREATE_SUSPENDED = 0x00000004
INVALID_HANDLE = ctypes.c_void_p(-1).value

# Structures for CreateProcessW
class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]

# PROCESS_BASIC_INFORMATION for NtQueryInformationProcess
class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


class RobloxManager:
    """Manages Roblox process attachment, memory read/write, and JSON flag application."""

    @staticmethod
    def fetch_offsets(force_rescan=False):
        """Fetch pre-computed FFlag RVA offsets from local memory scanner."""
        global _cached_offsets
        if _cached_offsets is not None and not force_rescan:
            return _cached_offsets
            
        version_dir = RobloxManager.get_roblox_version_dir()
        if not version_dir:
            log("[-] Cannot find Roblox version directory.", (255, 100, 100))
            return {}
            
        version = os.path.basename(version_dir)
        cache_file = scanner.get_cache_file(version)
        
        if not force_rescan and os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    offsets = json.load(f)
                _cached_offsets = offsets
                size = len(offsets)
                log(f"[+] Loaded {size} offsets from local cache ({version})", (100, 255, 100))
                return offsets
            except Exception as e:
                log(f"[-] Local cache corrupt, rescanning... ({e})", (200, 200, 100))
                
        # Execute Native Memory Scan
        try:
            log("[*] [Scanner] Spawning Roblox offline to scrape RVAs...", (200, 200, 100))
            exe_path = os.path.join(version_dir, "RobloxPlayerBeta.exe")
            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(STARTUPINFOW)
            si.dwFlags = 0x00000001 | 0x00000800 # STARTF_USESHOWWINDOW | STARTF_FORCEOFFFEEDBACK
            si.wShowWindow = 0 # SW_HIDE
            pi = PROCESS_INFORMATION()
            
            # Combine CREATE_NO_WINDOW with a background priority class to reduce impact
            # 0x08000000 = CREATE_NO_WINDOW
            # 0x00000040 = IDLE_PRIORITY_CLASS
            creation_flags = 0x08000000 | 0x00000040
            
            success = _k32.CreateProcessW(
                exe_path, None, None, None, False,
                creation_flags, None, version_dir,
                ctypes.byref(si), ctypes.byref(pi)
            )
            if not success:
                log("[-] [Scanner] Failed to spawn process.", (255, 100, 100))
                return {}
                
            log("[*] [Scanner] Waiting for Hyperion to unpack .text section...", (200, 200, 100))
            
            pbi = PROCESS_BASIC_INFORMATION()
            ret_len = ctypes.c_ulong(0)
            _ntdll.NtQueryInformationProcess(pi.hProcess, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len))
            
            base_buf = ctypes.create_string_buffer(8)
            bytes_read = ctypes.c_size_t(0)
            _ntdll.NtReadVirtualMemory(pi.hProcess, ctypes.c_void_p(pbi.PebBaseAddress + 0x10), base_buf, 8, ctypes.byref(bytes_read))
            image_base = struct.unpack("<Q", base_buf.raw[:8])[0]
            
            s = scanner.FFlagScanner(pi.hProcess, image_base)
            
            # Poll until .text section is readable (no longer PAGE_NOACCESS)
            unpacked = False
            for _ in range(15):
                s.parse_pe_headers()
                text_sec = s.sections.get(".text")
                if text_sec:
                    mbi = MEMORY_BASIC_INFORMATION()
                    _k32.VirtualQueryEx(pi.hProcess, ctypes.c_void_p(text_sec["addr"]), ctypes.byref(mbi), ctypes.sizeof(mbi))
                    if mbi.Protect != 1: # 1 == PAGE_NOACCESS
                        unpacked = True
                        break
                time.sleep(1.0)
                
            if not unpacked:
                log("[-] [Scanner] Timed out waiting for Hyperion unpack.", (255, 100, 100))
                _k32.TerminateProcess(pi.hProcess, 0)
                return {}
                
            offsets = s.dump_fflags()
            
            _k32.TerminateProcess(pi.hProcess, 0)
            _k32.CloseHandle(pi.hThread)
            _k32.CloseHandle(pi.hProcess)
            
            if offsets:
                with open(cache_file, 'w') as f:
                    json.dump(offsets, f)
                _cached_offsets = offsets
                return offsets
        except Exception as e:
            log(f"[-] [Scanner] Failed natively: {e}", (255, 100, 100))
            
        return {}

    @staticmethod
    def get_offset_for_flag(flag_name):
        """Get the RVA hex offset for a specific flag name.
        Handles prefixed/unprefixed variations by normalizing to 'clean' names.
        """
        offsets = RobloxManager.fetch_offsets()
        if not offsets:
            return None
            
        def extract_hex(data):
            if isinstance(data, dict):
                return data.get('offset')
            return data

        # Direct match (fastest/primary)
        if flag_name in offsets:
            return extract_hex(offsets[flag_name])
            
        # Fuzzy match: try to find by normalized name
        from src.utils.helpers import clean_flag_name
        clean_target = clean_flag_name(flag_name)
        
        for full_name, data in offsets.items():
            if clean_flag_name(full_name) == clean_target:
                return extract_hex(data)
                
        return None

    @staticmethod
    def get_all_roblox_version_dirs():
        """Find ALL valid Roblox version directories found on the system."""
        local = os.environ.get("LOCALAPPDATA", "")
        
        # STEP 1: Known Launcher Root Search
        roots = [
            os.path.join(local, "Roblox", "Versions"),
            os.path.join(local, "Bloxstrap", "Versions"),
            os.path.join(local, "Voidstrap", "RblxVersions"),
            os.path.join(local, "Fishstrap", "Versions"),
            os.path.join(local, "Froststrap", "Versions"),
            os.path.join(local, "Plexity", "Versions")
        ]
        
        candidates = []
        for vdir_root in roots:
            if not os.path.isdir(vdir_root):
                continue
            for d in os.listdir(vdir_root):
                path = os.path.join(vdir_root, d)
                if os.path.isdir(path):
                    # Check for executables (Beta or standard)
                    if any(os.path.exists(os.path.join(path, f)) for f in ["RobloxPlayerBeta.exe", "RobloxPlayer.exe"]):
                        candidates.append(path)
        
        # Also check current running process for an active path
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Roblox")
            if hwnd:
                pid = ctypes.c_ulong(0)
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value > 0:
                    h_proc = _k32.OpenProcess(0x1000 | 0x0010, False, pid.value)
                    if h_proc:
                        exe_path = (ctypes.c_wchar * 260)()
                        size = ctypes.c_uint(260)
                        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h_proc, 0, exe_path, ctypes.byref(size)):
                            vdir = os.path.dirname(exe_path.value)
                            if os.path.isdir(vdir) and vdir not in candidates:
                                candidates.append(vdir)
                        _k32.CloseHandle(h_proc)
        except Exception:
            pass
            
        return candidates

    @staticmethod
    def get_roblox_version_dir():
        """Find the single best (most recent) Roblox version directory."""
        candidates = RobloxManager.get_all_roblox_version_dirs()
        if not candidates:
            return None
            
        # Sort by most recently used/modified
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return candidates[0]

    @staticmethod
    def apply_fflags_json(flags_dict):
        """Write FFlags to ClientAppSettings.json across ALL detected versions (Scatter-Sync)."""
        vdirs = RobloxManager.get_all_roblox_version_dirs()
        if not vdirs:
            return False, "No Roblox version directories found"

        success_count = 0
        errors = []
        
        for vdir in vdirs:
            settings_dir = os.path.join(vdir, "ClientSettings")
            settings_file = os.path.join(settings_dir, "ClientAppSettings.json")
            
            try:
                os.makedirs(settings_dir, exist_ok=True)
                with open(settings_file, 'w', encoding='utf-8') as f:
                    json.dump(flags_dict, f, indent=4)
                success_count += 1
            except Exception as e:
                errors.append(f"{os.path.basename(vdir)}: {e}")
        
        if success_count > 0:
            return True, f"Synced flags to {success_count} Roblox versions"
        return False, f"Failed to write to any versions: {', '.join(errors)}"

    # ================================================================
    # Instance methods
    # ================================================================

    def __init__(self):
        self.pid = None
        self.is_attached = False
        self.attach_time = 0
        self.base_address = 0
        self._h_process = None  # HANDLE (pointer-sized)
        self._lock = threading.Lock()

    def kill_roblox(self):
        """Kill all running Roblox processes."""
        killed = 0
        try:
            snapshot = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snapshot == INVALID_HANDLE:
                return 0
            
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            
            if _k32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.szExeFile.lower() == "robloxplayerbeta.exe":
                        pid = entry.th32ProcessID
                        h = _k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
                        if h:
                            _k32.TerminateProcess(h, 0)
                            _k32.CloseHandle(h)
                            killed += 1
                    if not _k32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
            _k32.CloseHandle(snapshot)
        except Exception:
            pass
        
        # Reset state
        if self._h_process:
            _k32.CloseHandle(self._h_process)
        self._h_process = None
        self.pid = None
        self.is_attached = False
        self.base_address = 0
        
        return killed

    def find_roblox_process(self):
        """Find the live Roblox process PID by looking for the visible game window.
        This ignores background zombie processes and invisible crash handlers.
        """
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Roblox")
            if hwnd:
                pid = ctypes.c_ulong(0)
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value > 0:
                    # Double check it is actually Roblox
                    snapshot = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                    if snapshot != INVALID_HANDLE:
                        entry = PROCESSENTRY32W()
                        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                        
                        if _k32.Process32FirstW(snapshot, ctypes.byref(entry)):
                            while True:
                                if entry.th32ProcessID == pid.value and entry.szExeFile.lower() == "robloxplayerbeta.exe":
                                    _k32.CloseHandle(snapshot)
                                    return pid.value
                                if not _k32.Process32NextW(snapshot, ctypes.byref(entry)):
                                    break
                        _k32.CloseHandle(snapshot)
        except Exception:
            pass
        return None

    def attach(self):
        """Find Roblox and attach for external write."""
        pid = self.find_roblox_process()
        if not pid:
            self.reset()
            return False

        # If PID changed, reset handle
        if self.pid != pid:
            self._close_handle()
            self.base_address = 0
            self.attach_time = time.time()

        self.pid = pid
        self.is_attached = True
        return True

    def reset(self):
        """Reset all state."""
        self._close_handle()
        self.pid = None
        self.is_attached = False
        self.attach_time = 0
        self.base_address = 0

    def _close_handle(self):
        """Safely close the process handle."""
        if self._h_process:
            try:
                _k32.CloseHandle(self._h_process)
            except Exception:
                pass
            self._h_process = None

    def find_pattern(self, pattern, module_name="RobloxPlayerBeta.exe"):
        if not self._h_process and not self.attach():
            return None
            
        import re
        
        # Get module base and size
        if not self.base_address:
            # Simple fallback if base_address is not yet resolved
            return None
            
        # We'll scan a reasonable chunk of the .text / .data section
        # For simplicity in this implementation, scan first 100MB
        scan_size = 100 * 1024 * 1024
        data = self.read_memory_external(self.base_address, scan_size)
        if not data:
            return None
            
        # Convert hex pattern to regex
        regex_pattern = b""
        for part in pattern.split():
            if part == "??":
                regex_pattern += b"."
            else:
                regex_pattern += re.escape(bytes.fromhex(part))
        
        match = re.search(regex_pattern, data)
        if match:
            return self.base_address + match.start()
        return None

    def write_memory_external(self, addr, data):
        """Write raw bytes to a target address in the Roblox process."""
        if not self._h_process:
            if not self.open_process_for_write():
                return False, "Cannot open process"
        
        size = len(data)
        buf = ctypes.create_string_buffer(data)
        bytes_written = ctypes.c_size_t(0)
        
        # Try NtWriteVirtualMemory first
        status = _ntdll.NtWriteVirtualMemory(
            self._h_process, ctypes.c_void_p(addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_written)
        )
        
        if status == 0 and bytes_written.value == size:
            return True, f"OK|NtWrite (0x{addr:X})"
        
        # Fallback: VirtualProtectEx + WriteProcessMemory
        old_protect = wintypes.DWORD(0)
        vp_ok = _k32.VirtualProtectEx(
            self._h_process, ctypes.c_void_p(addr),
            ctypes.c_size_t(size), PAGE_READWRITE, ctypes.byref(old_protect)
        )
        
        if vp_ok:
            wpm_bytes = ctypes.c_size_t(0)
            success = _k32.WriteProcessMemory(
                self._h_process, ctypes.c_void_p(addr),
                buf, ctypes.c_size_t(size), ctypes.byref(wpm_bytes)
            )
            # Restore original protection
            restored = wintypes.DWORD(0)
            _k32.VirtualProtectEx(
                self._h_process, ctypes.c_void_p(addr),
                ctypes.c_size_t(size), old_protect.value, ctypes.byref(restored)
            )
            if success and wpm_bytes.value == size:
                return True, f"OK|VP+WPM (0x{addr:X})"
        
        return False, f"Write failed at 0x{addr:X}"

    def write_fps_direct(self, value):
        """Directly overwrite the TaskScheduler target FPS singleton."""
        if not self.attach():
            return False, "Not attached"
            
        pattern = "48 8B 05 ?? ?? ?? ?? 48 8B D1 48 8B 0C"
        addr = self.find_pattern(pattern)
        if not addr:
            return False, "Pattern not found"
            
        offset_bytes = self.read_memory_external(addr + 3, 4)
        if not offset_bytes:
            return False, "Failed to read offset"
            
        rel_offset = struct.unpack("<i", offset_bytes)[0]
        # Pointer is at instruction_end + rel_offset
        ptr_addr = addr + 7 + rel_offset
        
        # Read the actual Instance pointer
        inst_ptr_bytes = self.read_memory_external(ptr_addr, 8)
        if not inst_ptr_bytes:
            return False, "Failed to read Instance pointer"
            
        inst_ptr = struct.unpack("<Q", inst_ptr_bytes)[0]
        if inst_ptr == 0:
            return False, "Instance pointer is NULL"
            
        # TaskSchedulerTargetFps is at offset 0x118 (verified in sandbox)
        fps_addr = inst_ptr + 0x118
        
        # Write the 4-byte int
        data = struct.pack("<i", value)
        ok, msg = self.write_memory_external(fps_addr, data)
        return ok, msg

    # ================================================================

    def open_process_for_write(self):
        """Open Roblox process with VM read/write/operation permissions."""
        if not self.pid:
            return False
        
        if self._h_process:
            return True  # Already open
        
        handle = _k32.OpenProcess(PROCESS_ACCESS, False, self.pid)
        if not handle:
            err = ctypes.get_last_error()
            log(f"[-] OpenProcess failed (err {err})", (255, 100, 100))
            return False
        
        self._h_process = handle
        return True

    def get_roblox_base(self):
        """Get the base address of RobloxPlayerBeta.exe using PEB traversal to bypass module hiding."""
        if self.base_address:
            return self.base_address
        
        if not self._h_process:
            if not self.open_process_for_write():
                return 0
                
        try:
            pbi = PROCESS_BASIC_INFORMATION()
            ret_len = ctypes.c_ulong(0)
            status = _ntdll.NtQueryInformationProcess(
                self._h_process, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
            )
            
            if status == 0 and pbi.PebBaseAddress:
                base_buf = ctypes.create_string_buffer(8)
                bytes_read = ctypes.c_size_t(0)
                # Read ImageBaseAddress from PEB (offset 0x10 on x64)
                success = _ntdll.NtReadVirtualMemory(
                    self._h_process, ctypes.c_void_p(pbi.PebBaseAddress + 0x10), 
                    base_buf, 8, ctypes.byref(bytes_read)
                )
                if success == 0 and bytes_read.value == 8:
                    self.base_address = struct.unpack("<Q", base_buf.raw[:8])[0]
                    log(f"[+] Roblox base (PEB): 0x{self.base_address:X}", (100, 255, 100))
                    return self.base_address
                    
            log(f"[-] PEB Query failed (status 0x{status:X})", (255, 100, 100))
        except Exception as e:
            log(f"[-] get_roblox_base error: {e}", (255, 100, 100))
            
        return 0

    def write_flag_external(self, flag_name, flag_type, offset, value):
        if not self._h_process:
            if not self.open_process_for_write():
                return False, "Cannot open process"
        
        base = self.get_roblox_base()
        if not base:
            return False, "Cannot find base address"
        
        addr = base + offset
        
        # Prepare the value bytes based on type using Safer Encoding Pipeline rules
        if flag_type == "bool":
            # Force precisely 1 byte
            val = str(value).lower() in ("true", "1", "yes")
            data = struct.pack("<B", 1 if val else 0)
            size = 1
        elif flag_type == "int":
            # Strict clamping
            try:
                v = int(value)
                v = max(-2147483648, min(2147483647, v))
                data = struct.pack("<i", v)
                size = 4
            except (ValueError, struct.error):
                return False, f"Invalid int value: {value}"
        elif flag_type == "float":
            # Roblox uses 8-byte doubles for decimal points
            try:
                data = struct.pack("<d", float(value))
                size = 8
            except (ValueError, struct.error):
                return False, f"Invalid float value: {value}"
        elif flag_type == "string":
            # Strictly encode to 255 max + null padding for fixed buffer writes
            try:
                enc = str(value).encode('utf-8')[:255]
                data = enc + b'\x00'
                size = len(data)
            except Exception:
                return False, f"Invalid string formatting: {value}"
        else:
            return False, f"Unsupported memory write type: {flag_type}"
        
        buf = ctypes.create_string_buffer(data)
        bytes_written = ctypes.c_size_t(0)
        
        # Method 1: NtWriteVirtualMemory (works on .data sections, no protection change)
        status = _ntdll.NtWriteVirtualMemory(
            self._h_process, ctypes.c_void_p(addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_written)
        )
        
        if status == 0 and bytes_written.value == size:
            return True, f"OK|NtWrite (0x{addr:X})"
        
        # Method 2: VirtualProtectEx → WriteProcessMemory → Restore
        old_protect = wintypes.DWORD(0)
        vp_ok = _k32.VirtualProtectEx(
            self._h_process, ctypes.c_void_p(addr),
            ctypes.c_size_t(size), PAGE_READWRITE, ctypes.byref(old_protect)
        )
        
        if vp_ok:
            wpm_bytes = ctypes.c_size_t(0)
            success = _k32.WriteProcessMemory(
                self._h_process, ctypes.c_void_p(addr),
                buf, ctypes.c_size_t(size), ctypes.byref(wpm_bytes)
            )
            
            # Restore original protection
            restored = wintypes.DWORD(0)
            _k32.VirtualProtectEx(
                self._h_process, ctypes.c_void_p(addr),
                ctypes.c_size_t(size), old_protect.value, ctypes.byref(restored)
            )
            
            if success and wpm_bytes.value == size:
                return True, f"OK|VP+WPM (0x{addr:X})"
            else:
                err = ctypes.get_last_error()
                return False, f"WPM failed after VirtualProtect (err: {err})"
        
        # VirtualProtectEx failed — section is locked, flag is covered by JSON
        return False, f"JSON-only (live write unavailable)"

    def read_flag_external(self, flag_type, offset):
        """Read a flag's original value from Roblox memory externally."""
        if not self._h_process:
            if not self.open_process_for_write(): return None
        base = self.get_roblox_base()
        if not base: return None
        
        addr = base + offset
        
        if flag_type == "bool":
            size = 1
        elif flag_type in ("int", "float"):
            size = 4 if flag_type == "int" else 8
        elif flag_type == "string":
            size = 255
        else:
            return None
            
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        
        status = _ntdll.NtReadVirtualMemory(
            self._h_process, ctypes.c_void_p(addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_read)
        )
        
        if status == 0 and bytes_read.value > 0:
            if flag_type == "bool":
                return "true" if struct.unpack("<B", buf.raw[:1])[0] != 0 else "false"
            elif flag_type == "int":
                return str(struct.unpack("<i", buf.raw[:4])[0])
            elif flag_type == "float":
                return str(round(struct.unpack("<d", buf.raw[:8])[0], 4))
            elif flag_type == "string":
                return buf.value.split(b'\x00')[0].decode('utf-8', errors='ignore')
        return None

    def read_memory_external(self, addr, size):
        """Read memory from Roblox process. Returns bytes or None."""
        if not self._h_process:
            if not self.open_process_for_write():
                return None
                
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        
        status = _ntdll.NtReadVirtualMemory(
            self._h_process, ctypes.c_void_p(addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_read)
        )
        
        if status == 0 and bytes_read.value > 0:
            return buf.raw[:bytes_read.value]
        return None

    def launch_and_patch_roblox(self, flags_list):
        # Find the exe
        version_dir = RobloxManager.get_roblox_version_dir()
        if not version_dir:
            log("[-] Cannot find Roblox version directory", (255, 100, 100))
            return False, 0, 0, 0
        
        exe_path = os.path.join(version_dir, "RobloxPlayerBeta.exe")
        if not os.path.exists(exe_path):
            log(f"[-] Roblox executable not found at {exe_path}", (255, 100, 100))
            return False, 0, 0, 0
        
        # Fetch offsets (needed to know RVAs)
        offsets = RobloxManager.fetch_offsets()
        if not offsets:
            log("[-] No offsets available for early patching", (255, 100, 100))
            return False, 0, 0, 0
        
        log(f"[*] Launching Roblox suspended for early patching...", (100, 255, 255))
        
        # Setup structures
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        pi = PROCESS_INFORMATION()
        
        # Create the process suspended
        success = _k32.CreateProcessW(
            exe_path, None, None, None, False,
            CREATE_SUSPENDED, None, version_dir,
            ctypes.byref(si), ctypes.byref(pi)
        )
        
        if not success:
            err = ctypes.get_last_error()
            log(f"[-] CreateProcessW failed (err: {err})", (255, 100, 100))
            return False, 0, 0, 0
        
        new_pid = pi.dwProcessId
        h_process = pi.hProcess
        h_thread = pi.hThread
        log(f"[+] Roblox spawned suspended (PID {new_pid})", (100, 255, 100))
        
        # Read PEB to get ImageBaseAddress
        pbi = PROCESS_BASIC_INFORMATION()
        ret_len = ctypes.c_ulong(0)
        status = _ntdll.NtQueryInformationProcess(
            h_process, 0,  # ProcessBasicInformation
            ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
        )
        
        if status != 0:
            log(f"[-] NtQueryInformationProcess failed (0x{status & 0xFFFFFFFF:08X})", (255, 100, 100))
            _k32.ResumeThread(h_thread)
            _k32.CloseHandle(h_thread)
            _k32.CloseHandle(h_process)
            return False, 0, 0, new_pid
        
        peb_addr = pbi.PebBaseAddress
        
        # Read ImageBaseAddress from PEB (at offset 0x10 on x64)
        # PEB layout: at +0x10 is ImageBaseAddress (PVOID)
        base_buf = ctypes.create_string_buffer(8)
        bytes_read = ctypes.c_size_t(0)
        peb_image_base_offset = peb_addr + 0x10
        
        status = _ntdll.NtReadVirtualMemory(
            h_process, ctypes.c_void_p(peb_image_base_offset),
            base_buf, ctypes.c_size_t(8), ctypes.byref(bytes_read)
        )
        
        if status != 0 or bytes_read.value < 8:
            log(f"[-] Failed to read ImageBaseAddress from PEB", (255, 100, 100))
            _k32.ResumeThread(h_thread)
            _k32.CloseHandle(h_thread)
            _k32.CloseHandle(h_process)
            return False, 0, 0, new_pid
        
        image_base = struct.unpack("<Q", base_buf.raw[:8])[0]
        log(f"[+] Image base: 0x{image_base:X}", (100, 255, 100))
        
        # Now patch all flags
        patched = 0
        attempted = 0
        
        for flag in flags_list:
            name = flag['name']
            value = flag['value']
            flag_type = flag.get('type', 'string')
            
            # Skip string flags
            if flag_type == 'string':
                continue
            
            offset_hex = offsets.get(name)
            if not offset_hex:
                continue
            
            try:
                offset_int = int(offset_hex, 16)
            except Exception:
                continue
            
            attempted += 1
            addr = image_base + offset_int
            
            # Pack the value
            if flag_type == "bool":
                val = str(value).lower() in ("true", "1", "yes")
                data = struct.pack("<B", 1 if val else 0)
                size = 1
            elif flag_type == "int":
                try:
                    data = struct.pack("<i", int(value))
                    size = 4
                except (ValueError, struct.error):
                    continue
            elif flag_type == "float":
                try:
                    data = struct.pack("<d", float(value))
                    size = 8
                except (ValueError, struct.error):
                    continue
            else:
                continue
            
            # Capture original value before we patch it
            if 'original_value' not in flag:
                orig_buf = ctypes.create_string_buffer(size)
                r_bw = ctypes.c_size_t(0)
                r_status = _ntdll.NtReadVirtualMemory(
                    h_process, ctypes.c_void_p(addr),
                    orig_buf, ctypes.c_size_t(size), ctypes.byref(r_bw)
                )
                if r_status == 0 and r_bw.value == size:
                    if flag_type == "bool":
                        flag['original_value'] = "true" if struct.unpack("<B", orig_buf.raw[:1])[0] != 0 else "false"
                    elif flag_type == "int":
                        flag['original_value'] = str(struct.unpack("<i", orig_buf.raw[:4])[0])
                    elif flag_type == "float":
                        flag['original_value'] = str(round(struct.unpack("<d", orig_buf.raw[:8])[0], 4))

            # ONLY PATCH IF ENABLED
            isEnabled = flag.get('enabled', True)
            if not isEnabled:
                continue

            buf = ctypes.create_string_buffer(data)
            bw = ctypes.c_size_t(0)
            
            w_status = _ntdll.NtWriteVirtualMemory(
                h_process, ctypes.c_void_p(addr),
                buf, ctypes.c_size_t(size), ctypes.byref(bw)
            )
            
            if w_status == 0 and bw.value == size:
                patched += 1
                flag['_status'] = 'success'
            else:
                # Try WriteProcessMemory as fallback
                wpm_bw = ctypes.c_size_t(0)
                wpm_ok = _k32.WriteProcessMemory(
                    h_process, ctypes.c_void_p(addr),
                    buf, ctypes.c_size_t(size), ctypes.byref(wpm_bw)
                )
                if wpm_ok and wpm_bw.value == size:
                    patched += 1
                    flag['_status'] = 'success'
                else:
                    log(f"[·] Early patch failed: {name} (0x{w_status & 0xFFFFFFFF:08X})", (200, 200, 100))
        
        log(f"[+] Early patch: {patched}/{attempted} flags written before Hyperion", (100, 255, 100))
        
        # Resume the process
        _k32.ResumeThread(h_thread)
        self.attach_time = time.time()
        log(f"[+] Roblox resumed (PID {new_pid})", (100, 255, 100))
        
        # Close handles for thread (we keep process handle for attaching)
        _k32.CloseHandle(h_thread)
        
        # Auto-attach to the new process
        self.pid = new_pid
        self.is_attached = True
        self._h_process = h_process
        self.base_address = image_base
        
        return True, patched, attempted, new_pid

