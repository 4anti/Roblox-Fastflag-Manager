import os
import sys
import time
import struct
import subprocess
import threading
import ctypes
import ctypes.wintypes as wintypes
import json
import re
import urllib.request
from src.utils.logger import log

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
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]

_k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t

# ================================================================
# Per-session caches
# ================================================================

# Live flag address cache (per-session, invalidated on PID change)
_live_flag_cache = {}      # {clean_name: [{"abs_addr": int, "full_name": str, "type": str}, ...]}
_live_flag_cache_pid = None  # PID this cache is valid for

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
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Hyperion bypass: full QUERY_INFORMATION (0x400) is denied, but the 0x38 mask
# (VM_OPERATION | VM_READ | VM_WRITE) survives. Matches the test_unstickforce_v4 reference.
PROCESS_ACCESS_STEALTH = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
PROCESS_ACCESS = PROCESS_ACCESS_STEALTH

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
    def get_roblox_version_string():
        """Get the unique version string (e.g. version-a1b2c3...) of the current Roblox install."""
        vdir = RobloxManager.get_roblox_version_dir()
        if not vdir: return "unknown"
        return os.path.basename(vdir)

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

    def __init__(self, pid=None):
        self.pid = pid
        self._h_process = None  # HANDLE (pointer-sized)
        self._base_address = None
        self._version_dir = None
        self.is_attached = False
        self.attach_time = 0
        self.base_address = 0
        self._lock = threading.Lock()
        # Stealth-syscall stub for Hyperion bypass on .data writes. Without
        # FlogBank's heap fallback, every write hits the (often locked) .data
        # arena, so this is now load-bearing — auto-init and let it stay None
        # only if stub construction fails on this host.
        try:
            from src.core.syscall_manager import SyscallManager
            self.syscall_manager = SyscallManager()
        except Exception as e:
            log(f"[!] SyscallManager init failed: {e} — falling back to standard NtWrite", (255, 200, 100))
            self.syscall_manager = None

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
        self.invalidate_live_cache()

    def _close_handle(self):
        """Safely close the process handle."""
        if self._h_process:
            try:
                _k32.CloseHandle(self._h_process)
            except Exception:
                pass
            self._h_process = None


    def find_pattern(self, pattern_str, scan_size=None):
        """Find a byte pattern in the Roblox module."""
        if not self._h_process or not self.get_roblox_base():
            return None

        base = self.get_roblox_base()
        if scan_size is None:
            scan_size = 150 * 1024 * 1024

        pattern_parts = pattern_str.split()
        re_pat = b""
        for p in pattern_parts:
            if p == "??":
                re_pat += b"."
            else:
                re_pat += re.escape(bytes.fromhex(p))
        regex = re.compile(re_pat, re.DOTALL)

        chunk_size = 10 * 1024 * 1024
        for offset in range(0, scan_size, chunk_size - 128):
            data = self.read_memory_external(base + offset, chunk_size)
            if not data:
                continue
            match = regex.search(data)
            if match:
                return base + offset + match.start()
        return None

    def write_memory_external(self, addr, data):
        """Write raw bytes to a target address in the Roblox process with robust safety."""
        if not self._h_process:
            if not self.open_process_for_write():
                return False, "Cannot open process"
        
        size = len(data)
        buf = ctypes.create_string_buffer(data)
        bytes_written = ctypes.c_size_t(0)
        
        # 1. Try Stealth NtWrite first
        status = _ntdll.NtWriteVirtualMemory(
            self._h_process, ctypes.c_void_p(addr),
            ctypes.byref(buf), ctypes.c_size_t(size), ctypes.byref(bytes_written)
        )
        
        if status == 0 and bytes_written.value == size:
            return True, f"OK|NtWrite (0x{addr:X})"
        
        # 2. Fallback: VirtualProtectEx + WriteProcessMemory
        old_protect = wintypes.DWORD(0)
        # Use 0x40 (PAGE_EXECUTE_READWRITE) to be absolutely sure we can write
        if _k32.VirtualProtectEx(self._h_process, ctypes.c_void_p(addr), ctypes.c_size_t(size), 0x40, ctypes.byref(old_protect)):
            success = _k32.WriteProcessMemory(
                self._h_process, ctypes.c_void_p(addr),
                ctypes.byref(buf), ctypes.c_size_t(size), ctypes.byref(bytes_written)
            )
            # Restore original protection
            _k32.VirtualProtectEx(self._h_process, ctypes.c_void_p(addr), ctypes.c_size_t(size), old_protect, ctypes.byref(wintypes.DWORD(0)))
            
            if success and bytes_written.value == size:
                return True, f"OK|VP+WPM (0x{addr:X})"
        
        err = ctypes.get_last_error()
        return False, f"ERR|NtStatus:0x{status:X}|WinErr:{err} (0x{addr:X})"

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

    def open_process_for_write(self, write_access=True):
        """Open Roblox process with the Hyperion-bypass mask (0x38).

        Hyperion blocks OpenProcess for masks containing PROCESS_QUERY_INFORMATION
        (0x400). The 0x38 mask (VM_OPERATION | VM_READ | VM_WRITE) survives and is
        what test_unstickforce_v4 uses. Read-only callers fall back further to 0x10.
        """
        if not self.pid:
            return False

        if self._h_process:
            return True

        if write_access:
            ladder = [PROCESS_ACCESS_STEALTH, PROCESS_ACCESS_STEALTH | PROCESS_QUERY_LIMITED_INFORMATION]
        else:
            ladder = [PROCESS_VM_READ, PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION]

        handle = None
        last_err = 0
        for access in ladder:
            handle = _k32.OpenProcess(access, False, self.pid)
            if handle:
                break
            last_err = ctypes.get_last_error()

        if not handle:
            log(f"[-] OpenProcess failed (err {last_err})", (255, 100, 100))
            return False

        self._h_process = handle
        return True

    def get_roblox_base(self):
        """Get the base address of RobloxPlayerBeta.exe.

        Tries PEB traversal first (works when handle has QUERY_LIMITED_INFORMATION),
        then falls back to a Toolhelp32 module walk which only needs the PID.
        Toolhelp32 is the path that survives the Hyperion 0x38-only handle.
        """
        if self.base_address:
            return self.base_address

        if not self._h_process:
            if not self.open_process_for_write():
                # PEB read needs a handle but Toolhelp32 doesn't — keep going.
                pass

        # Path A: PEB traversal (only works if handle includes QUERY_LIMITED_INFORMATION)
        if self._h_process:
            try:
                pbi = PROCESS_BASIC_INFORMATION()
                ret_len = ctypes.c_ulong(0)
                status = _ntdll.NtQueryInformationProcess(
                    self._h_process, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
                )
                if status == 0 and pbi.PebBaseAddress:
                    base_buf = ctypes.create_string_buffer(8)
                    bytes_read = ctypes.c_size_t(0)
                    rd = _ntdll.NtReadVirtualMemory(
                        self._h_process, ctypes.c_void_p(pbi.PebBaseAddress + 0x10),
                        base_buf, 8, ctypes.byref(bytes_read)
                    )
                    if rd == 0 and bytes_read.value == 8:
                        self.base_address = struct.unpack("<Q", base_buf.raw[:8])[0]
                        log(f"[+] Roblox base (PEB): 0x{self.base_address:X}", (100, 255, 100))
                        return self.base_address
            except Exception:
                pass

        # Path B: Toolhelp32 module enumeration (PID-only, survives 0x38 handle)
        try:
            snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
            if snap and snap != INVALID_HANDLE:
                me = MODULEENTRY32W()
                me.dwSize = ctypes.sizeof(MODULEENTRY32W)
                if _k32.Module32FirstW(snap, ctypes.byref(me)):
                    while True:
                        if me.szModule.lower() == "robloxplayerbeta.exe":
                            self.base_address = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value or 0
                            _k32.CloseHandle(snap)
                            log(f"[+] Roblox base (Toolhelp32): 0x{self.base_address:X}", (100, 255, 100))
                            return self.base_address
                        if not _k32.Module32NextW(snap, ctypes.byref(me)):
                            break
                _k32.CloseHandle(snap)
        except Exception as e:
            log(f"[-] get_roblox_base error: {e}", (255, 100, 100))

        log("[-] Could not resolve Roblox base address", (255, 100, 100))
        return 0

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

    def query_region(self, addr):
        """Query the memory region containing addr. Returns dict with state/protect/type/region keys, or None.

        Used to classify a target address before/after a failed write so we can decide
        whether the page is in .rdata (read-only image), .data (writable image), or heap.
        """
        if not self._h_process:
            if not self.open_process_for_write():
                return None
        mbi = MEMORY_BASIC_INFORMATION()
        ret = _k32.VirtualQueryEx(self._h_process, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ret:
            return None
        return {
            "base": mbi.BaseAddress or 0,
            "alloc_base": mbi.AllocationBase or 0,
            "size": mbi.RegionSize,
            "state": mbi.State,        # 0x1000 = COMMIT, 0x2000 = RESERVE, 0x10000 = FREE
            "protect": mbi.Protect,    # 0x02 RO, 0x04 RW, 0x20 ERX, 0x40 ERW, 0x80 EWC
            "type": mbi.Type,          # 0x1000000 IMAGE, 0x40000 MAPPED, 0x20000 PRIVATE
        }

    def is_writable_protect(self, protect):
        """True if the protection allows direct write without VirtualProtect."""
        # PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY
        return bool(protect & 0xCC)  # 0x04 | 0x08 | 0x40 | 0x80

    # ================================================================
    # Live Memory Injection (scans the running process directly)
    # ================================================================

    def clear_bank_cache(self):
        """Flush the per-session live-flag address cache.

        Name kept for back-compat with existing callers; FlogBank itself was
        removed in the imtheo-only refactor.
        """
        global _live_flag_cache, _live_flag_cache_pid
        _live_flag_cache = {}
        _live_flag_cache_pid = None
        log("[*] Live flag address cache flushed.", (180, 180, 180))

    def scan_live_flags(self, target_names: list[str] | None = None, force_rescan: bool = False) -> dict[str, list[dict]]:
        """Resolve live flag addresses purely from Imtheo's RVA map (+ disk cache).

        Imtheo-only after the FlogBank removal: every entry is a single
        ``base + RVA`` address in the .data arena. Hyperion-locked pages will
        return False from ``write_flag_at_address`` and the JSON path covers
        them.
        """
        global _live_flag_cache, _live_flag_cache_pid
        if not force_rescan and _live_flag_cache_pid == self.pid and _live_flag_cache:
            return _live_flag_cache

        if not self.is_attached:
            return {}
        base = self.get_roblox_base()
        if not base:
            return {}

        from src.utils.helpers import clean_flag_name
        clean_targets = {clean_flag_name(n) for n in target_names} if target_names else None

        flag_offsets, _ = self._fetch_offset_sources(clean_targets)

        live_addrs: dict[str, list[dict]] = {}
        for clean, data in flag_offsets.items():
            live_addrs[clean] = [{
                "abs_addr": data["abs_addr"],
                "full_name": data["full_name"],
                "type": data["type"],
                "source": "imtheo",
            }]

        log(f"[+] Live scan resolved {len(live_addrs)} flags via Imtheo RVAs",
            (100, 255, 100))

        if live_addrs:
            _live_flag_cache = live_addrs
            _live_flag_cache_pid = self.pid

        return live_addrs

    def _fetch_offset_sources(self, clean_targets=None):
        """Resolve flag RVAs + FFlagList struct offsets via offset_loader.

        Imtheo FFlags.hpp is the sole offset source (+ disk cache on failure).
        """
        from src.core import offset_loader
        base = self.get_roblox_base()
        if not base:
            return {}, {}
        return offset_loader.load_offsets(
            base_addr=base,
            build_version=RobloxManager.get_roblox_version_string(),
            user_flag_clean_names=clean_targets,
        )

    def get_live_flag_address(self, flag_name):
        """Get the cached live absolute address for a specific flag."""
        global _live_flag_cache, _live_flag_cache_pid
        
        if _live_flag_cache_pid != self.pid or not _live_flag_cache:
            return None
        
        from src.utils.helpers import clean_flag_name
        clean = clean_flag_name(flag_name)
        data = _live_flag_cache.get(clean) or _live_flag_cache.get(flag_name)
        return data

    def write_flag_at_address(self, flag_type, abs_addr, value):
        """Write a typed value at an absolute process address (no base offset).

        Returns (success: bool, message: str). On unwritable image pages (.rdata
        protected by Hyperion), returns (False, "JSON_ONLY|...") so the caller
        can downgrade the log level — the JSON path already covers those flags.
        """
        if not self._h_process:
            return False, "No process handle"

        # Pack value based on type
        if flag_type == "bool":
            val = str(value).lower() in ("true", "1", "yes")
            data = struct.pack("<B", 1 if val else 0)
        elif flag_type == "int":
            try:
                v = int(value)
                v = max(-2147483648, min(2147483647, v))
                data = struct.pack("<i", v)
            except (ValueError, struct.error):
                return False, f"Invalid int: {value}"
        elif flag_type == "float":
            try:
                # Roblox FFloat is single-precision (4 bytes). Writing 8 bytes here
                # overwrites the next field in the descriptor struct (desc+0xc4..0xc7),
                # corrupting it. Engine reads the corruption on game join → silent exit.
                data = struct.pack("<f", float(value))
            except (ValueError, struct.error):
                return False, f"Invalid float: {value}"
        else:
            return False, f"Unsupported type for memory write: {flag_type}"

        size = len(data)
        buf = ctypes.create_string_buffer(data)
        bw = ctypes.c_size_t(0)

        # 1. Standard ntdll write
        status = _ntdll.NtWriteVirtualMemory(
            self._h_process, ctypes.c_void_p(abs_addr),
            ctypes.byref(buf), ctypes.c_size_t(size), ctypes.byref(bw)
        )
        last_status = status
        if status == 0 and bw.value == size:
            return True, f"OK|NtWrite (0x{abs_addr:X})"

        # 2. VirtualProtectEx + WriteProcessMemory, try RW then ERW
        for new_prot in (0x04, 0x40):
            old_protect = wintypes.DWORD(0)
            if not _k32.VirtualProtectEx(
                self._h_process, ctypes.c_void_p(abs_addr),
                ctypes.c_size_t(size), new_prot, ctypes.byref(old_protect)
            ):
                continue
            wpm_bw = ctypes.c_size_t(0)
            ok = _k32.WriteProcessMemory(
                self._h_process, ctypes.c_void_p(abs_addr),
                ctypes.byref(buf), ctypes.c_size_t(size), ctypes.byref(wpm_bw)
            )
            restored = wintypes.DWORD(0)
            _k32.VirtualProtectEx(
                self._h_process, ctypes.c_void_p(abs_addr),
                ctypes.c_size_t(size), old_protect.value, ctypes.byref(restored)
            )
            if ok and wpm_bw.value == size:
                return True, f"OK|VP+WPM({hex(new_prot)}) (0x{abs_addr:X})"

        # All paths failed — classify the region so the caller can decide what to do.
        info = self.query_region(abs_addr)
        if info is None:
            return False, f"Write failed at 0x{abs_addr:X} (NtStatus: 0x{last_status & 0xFFFFFFFF:08X}, region unknown)"

        IMAGE = 0x1000000
        MAPPED = 0x40000          # MEM_MAPPED — file-backed section (Hyperion uses this for protected flag storage)
        COMMIT = 0x1000
        PROTECT_NOACCESS = 0x01
        PROTECT_READONLY = 0x02
        PROTECT_EXECUTE_READ = 0x20
        # Anything that includes WRITE access:
        WRITE_BITS = 0x04 | 0x08 | 0x40 | 0x80

        if info["state"] != COMMIT or info["protect"] == PROTECT_NOACCESS:
            return False, f"STALE_ADDR|state=0x{info['state']:X} protect=0x{info['protect']:02X} (0x{abs_addr:X})"

        # Read-only page in either image (.rdata) OR mapped section (Hyperion's locked
        # FFlag arena maps as MEM_MAPPED with PAGE_READONLY). Both are unwritable; the
        # JSON path covers these flags at engine startup, so this is expected.
        if info["protect"] in (PROTECT_READONLY, PROTECT_EXECUTE_READ) or not (info["protect"] & WRITE_BITS):
            kind = ".rdata" if info["type"] == IMAGE else ("mapped-locked" if info["type"] == MAPPED else f"type=0x{info['type']:X}")
            return False, f"JSON_ONLY|{kind} (0x{abs_addr:X}, protect=0x{info['protect']:02X})"

        return False, f"Write failed at 0x{abs_addr:X} (NtStatus: 0x{last_status & 0xFFFFFFFF:08X}, protect=0x{info['protect']:02X}, type=0x{info['type']:X})"

    def read_flag_at_address(self, flag_type, abs_addr):
        """Read a flag's current value from an absolute process address."""
        if not self._h_process:
            return None
        
        if flag_type == "bool":
            size = 1
        elif flag_type == "int":
            size = 4
        elif flag_type == "float":
            size = 4  # Roblox FFloat is single-precision
        else:
            return None

        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        status = _ntdll.NtReadVirtualMemory(
            self._h_process, ctypes.c_void_p(abs_addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_read)
        )

        if status == 0 and bytes_read.value == size:
            if flag_type == "bool":
                return "true" if struct.unpack("<B", buf.raw[:1])[0] != 0 else "false"
            elif flag_type == "int":
                return str(struct.unpack("<i", buf.raw[:4])[0])
            elif flag_type == "float":
                return str(round(struct.unpack("<f", buf.raw[:4])[0], 4))
        return None

    def invalidate_live_cache(self):
        """Clear all per-PID caches (call when Roblox restarts and PID changes)."""
        global _live_flag_cache, _live_flag_cache_pid
        _live_flag_cache = {}
        _live_flag_cache_pid = None

    def launch_and_patch_roblox(self, flags_list):
        """Launch Roblox normally. Early patching is removed because flags are heap-allocated."""
        # Find the exe
        version_dir = RobloxManager.get_roblox_version_dir()
        if not version_dir:
            log("[-] Cannot find Roblox version directory", (255, 100, 100))
            return False, 0, 0, 0
        
        exe_path = os.path.join(version_dir, "RobloxPlayerBeta.exe")
        if not os.path.exists(exe_path):
            log(f"[-] Roblox executable not found at {exe_path}", (255, 100, 100))
            return False, 0, 0, 0
            
        log("[*] Launching Roblox...", (100, 255, 255))
        
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        pi = PROCESS_INFORMATION()
        
        success = _k32.CreateProcessW(
            exe_path, None, None, None, False,
            0, None, version_dir,
            ctypes.byref(si), ctypes.byref(pi)
        )
        
        if success:
            log(f"[+] Roblox launched (PID {pi.dwProcessId})", (100, 255, 100))
            _k32.CloseHandle(pi.hThread)
            _k32.CloseHandle(pi.hProcess)
            self.pid = pi.dwProcessId
            return True, pi.dwProcessId, 0, 0
            
        err = ctypes.get_last_error()
        log(f"[-] Launch failed (err: {err})", (255, 100, 100))
        return False, 0, 0, 0

