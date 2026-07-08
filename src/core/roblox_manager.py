import os
import sys
import time
import struct
import array
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

# Allocate memory inside the target process — used to back FString flags whose
# new value is too long for std::string's inline (SSO) buffer.
_k32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
    wintypes.DWORD, wintypes.DWORD
]
_k32.VirtualAllocEx.restype = ctypes.c_void_p

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

# Serializes the global flag-address cache AND every process memory write across
# threads. Without this, the apply thread and the watchdog could rebuild/read the
# cache and run VirtualProtectEx/WriteProcessMemory on the same page at the same
# time — torn writes + wrong-address writes that corrupt Roblox memory and freeze
# /crash it (worst on preset switch). Reentrant so a locked call can nest safely.
_mem_lock = threading.RLock()

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
    def get_versions_root():
        """The parent 'Versions' directory of the most-recent install, or None."""
        vdir = RobloxManager.get_roblox_version_dir()
        if not vdir:
            return None
        return os.path.dirname(vdir)

    @staticmethod
    def get_stock_versions_root():
        """The STOCK Roblox versions root (%LOCALAPPDATA%\\Roblox\\Versions), or
        None. This is the ONLY install FFM writes flag files into — third-party
        bootstrapper installs (Bloxstrap/Fishstrap/Froststrap/Voidstrap/Plexity)
        are deliberately excluded (see get_writable_version_dirs)."""
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            return None
        return os.path.join(local, "Roblox", "Versions")

    @staticmethod
    def get_writable_version_dirs():
        """Version dirs FFM is allowed to WRITE flag files into: STOCK Roblox only.

        Third-party bootstrappers (Bloxstrap/Fishstrap/Froststrap/Voidstrap/
        Plexity) manage their own ClientAppSettings.json and copy user mods into
        their version dirs at launch. FFM must never overwrite those files — doing
        so would clobber the user's bootstrapper flags/settings. So flag file-sync
        is scoped to stock only; for a game launched by a third-party bootstrapper,
        FFM applies flags via live memory injection instead (never touching its
        files). Detection/injection still use get_all_roblox_version_dirs()."""
        root = RobloxManager.get_stock_versions_root()
        dirs = []
        if root and os.path.isdir(root):
            for d in os.listdir(root):
                path = os.path.join(root, d)
                if os.path.isdir(path) and any(
                    os.path.exists(os.path.join(path, f))
                    for f in ("RobloxPlayerBeta.exe", "RobloxPlayer.exe")
                ):
                    dirs.append(path)
        return dirs

    @staticmethod
    def resolve_version_exe(guid):
        """Resolve RobloxPlayerBeta.exe for a specific build guid (bare or
        'version-...'), or None if the versions root or the exe is missing."""
        root = RobloxManager.get_versions_root()
        if not root:
            return None
        name = guid if str(guid).startswith("version-") else f"version-{guid}"
        exe = os.path.join(root, name, "RobloxPlayerBeta.exe")
        return exe if os.path.exists(exe) else None

    @staticmethod
    def get_roblox_version_string():
        """Get the unique version string (e.g. version-a1b2c3...) of the current Roblox install."""
        vdir = RobloxManager.get_roblox_version_dir()
        if not vdir: return "unknown"
        return os.path.basename(vdir)

    @staticmethod
    def apply_fflags_json(flags_dict):
        """Write FFlags to ClientAppSettings.json across STOCK Roblox versions only.
        Third-party bootstrapper installs are intentionally skipped (their settings
        are theirs — FFM applies to their launches via memory injection)."""
        vdirs = RobloxManager.get_writable_version_dirs()
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

    @staticmethod
    def global_clientapp_path():
        """The legacy global ClientAppSettings.json living directly under
        %LOCALAPPDATA%\\Roblox\\ClientSettings (NOT inside a Versions\\ build).

        Roblox does not read this file, but other tools / older builds leave a
        flag set here. We include it when clearing so 'clean' is really clean.
        Returns None if LOCALAPPDATA is unavailable.
        """
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            return None
        return os.path.join(local, "Roblox", "ClientSettings", "ClientAppSettings.json")

    @staticmethod
    def _clientapp_targets(include_missing=True):
        """All ClientAppSettings.json paths FFM manages for clearing: every
        detected per-version dir plus the legacy global file. When
        include_missing is False, the global file is only listed if it exists
        (we never CREATE the global file — only clear one that's already there).
        """
        paths = [os.path.join(v, "ClientSettings", "ClientAppSettings.json")
                 for v in RobloxManager.get_writable_version_dirs()]
        gpath = RobloxManager.global_clientapp_path()
        if gpath and (include_missing or os.path.isfile(gpath)):
            paths.append(gpath)
        return paths

    @staticmethod
    def clientapp_json_has_flags():
        """True if ANY managed ClientAppSettings.json (per-version or the legacy
        global file) currently holds a non-empty flag set. Cheap probe used to
        avoid rewriting empty files on every idle tick."""
        for p in RobloxManager._clientapp_targets(include_missing=False):
            try:
                if not os.path.isfile(p):
                    continue
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and len(data) > 0:
                    return True
            except Exception:
                # Unreadable / not JSON: can't prove flags — skip it.
                continue
        return False

    @staticmethod
    def clear_fflags_json():
        """Overwrite ClientAppSettings.json with {} across ALL detected versions
        AND the legacy global file (if present).

        Used when FFM is not actively applying flags (app exit, Roblox exit,
        auto_apply disabled while Roblox is closed) so a subsequent Roblox
        launch starts with no leftover overrides. Scoped to STOCK versions only so
        we never blank a third-party bootstrapper's ClientAppSettings.json.
        """
        vdirs = RobloxManager.get_writable_version_dirs()

        success_count = 0
        errors = []

        for vdir in vdirs:
            settings_dir = os.path.join(vdir, "ClientSettings")
            settings_file = os.path.join(settings_dir, "ClientAppSettings.json")

            try:
                os.makedirs(settings_dir, exist_ok=True)
                with open(settings_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f)
                success_count += 1
            except Exception as e:
                errors.append(f"{os.path.basename(vdir)}: {e}")

        # Also clear the legacy global file, but only if it already exists — we
        # never create it where Roblox/other tools didn't.
        gpath = RobloxManager.global_clientapp_path()
        if gpath and os.path.isfile(gpath):
            try:
                with open(gpath, 'w', encoding='utf-8') as f:
                    json.dump({}, f)
                success_count += 1
            except Exception as e:
                errors.append(f"global: {e}")

        if success_count > 0:
            return True, f"Cleared ClientAppSettings.json in {success_count} location(s)"
        if not vdirs:
            return False, "No Roblox version directories found"
        return False, f"Failed to clear any locations: {', '.join(errors)}"

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
        # Reuse VirtualAllocEx buffers for heap-backed std::string flags so the
        # 5s watchdog re-enforcement doesn't allocate a fresh buffer every cycle.
        # Keyed by (pid, abs_addr, raw_bytes) -> remote pointer.
        self._string_buf_cache = {}
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

    @staticmethod
    def is_roblox_running():
        """True if a RobloxPlayerBeta.exe process is currently alive — regardless
        of whether its game window is up yet. Lets callers tell 'Roblox never
        started' apart from 'Roblox is running but we couldn't attach'."""
        try:
            snapshot = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snapshot == INVALID_HANDLE:
                return False
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            found = False
            if _k32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.szExeFile.lower() == "robloxplayerbeta.exe":
                        found = True
                        break
                    if not _k32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
            _k32.CloseHandle(snapshot)
            return found
        except Exception:
            return False

    @staticmethod
    def _launch_error_reason(err):
        """Human-readable cause for a CreateProcessW failure code."""
        return {
            2: "the Roblox executable is missing",
            3: "the Roblox path is invalid",
            5: "access denied — try running FFM as administrator",
            740: "Roblox needs administrator privileges (UAC)",
            1223: "the launch was cancelled",
        }.get(err, "Windows could not start the process")

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
        """Find a byte pattern (AOB) in the Roblox module.

        Walks committed, readable memory regions via VirtualQueryEx instead of
        reading blind fixed-size chunks. The old approach read 10 MB chunks and
        skipped an ENTIRE chunk whenever any page in it was unreadable — and the
        Hyperion-protected image is full of guard/unmapped pages, so large spans
        (potentially containing the target pattern) were silently never scanned.
        Region-walking + partial-read tolerance makes the scan robust, and the
        summary log line reports coverage so a real 'not found' can be told
        apart from a scan that was foiled by unreadable memory.
        """
        if not self._h_process or not self.get_roblox_base():
            return None

        base = self.get_roblox_base()
        if scan_size is None:
            scan_size = 300 * 1024 * 1024  # generous upper bound over the image

        pattern_parts = pattern_str.split()
        re_pat = b""
        for p in pattern_parts:
            if p == "??":
                re_pat += b"."
            else:
                re_pat += re.escape(bytes.fromhex(p))
        regex = re.compile(re_pat, re.DOTALL)
        pat_len = max(1, len(pattern_parts))

        PAGE = 0x1000
        chunk_size = 4 * 1024 * 1024
        end = base + scan_size

        regions_seen = 0
        regions_readable = 0
        read_fails = 0

        cursor = base
        while cursor < end:
            region = self.query_region(cursor)
            if not region:
                break
            regions_seen += 1
            r_base = region.get("base") or cursor
            r_size = region.get("size") or 0
            if r_size <= 0:
                cursor = r_base + PAGE
                continue
            nxt = r_base + r_size

            # Readable = committed (0x1000) and neither PAGE_NOACCESS (0x01)
            # nor PAGE_GUARD (0x100).
            committed = region.get("state") == 0x1000
            protect = region.get("protect") or 0
            readable = committed and not (protect & 0x101)

            if readable:
                regions_readable += 1
                carry = b""
                carry_addr = r_base
                off = 0
                while off < r_size:
                    want = min(chunk_size, r_size - off)
                    data = self.read_memory_external(r_base + off, want, allow_partial=True)
                    if not data:
                        read_fails += 1
                        off += PAGE          # skip the unreadable page, keep scanning
                        carry = b""
                        carry_addr = r_base + off
                        continue
                    window = carry + data
                    m = regex.search(window)
                    if m:
                        return carry_addr + m.start()
                    # Carry trailing (pat_len-1) bytes so a pattern straddling a
                    # chunk boundary is still matched on the next iteration.
                    if pat_len > 1:
                        carry = window[-(pat_len - 1):]
                        carry_addr = (r_base + off + len(data)) - len(carry)
                    else:
                        carry = b""
                        carry_addr = r_base + off + len(data)
                    off += len(data)

            cursor = nxt

        log(f"[scan] find_pattern: regions={regions_seen} readable={regions_readable} "
            f"read_fails={read_fails} -> NOT FOUND", (180, 180, 180))
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

    def read_memory_external(self, addr, size, allow_partial=False):
        """Read memory from Roblox process. Returns bytes or None.

        allow_partial: when True, also accept STATUS_PARTIAL_COPY (0x8000000D)
        and return the bytes copied before the first unreadable page. Used by
        AOB scanning so a single guard/unmapped page mid-range doesn't blind
        the whole read. Default False keeps strict all-or-nothing semantics for
        callers that need an exact-size read (e.g. pointer/struct reads)."""
        if not self._h_process:
            if not self.open_process_for_write():
                return None

        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)

        status = _ntdll.NtReadVirtualMemory(
            self._h_process, ctypes.c_void_p(addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_read)
        )

        if bytes_read.value > 0 and (
            status == 0 or (allow_partial and (status & 0xFFFFFFFF) == 0x8000000D)
        ):
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
        """Locking wrapper — serializes the cache fast-path/rebuild against
        concurrent writes + reads (apply thread vs watchdog). See _mem_lock."""
        with _mem_lock:
            return self._scan_live_flags_impl(target_names, force_rescan)

    def _scan_live_flags_impl(self, target_names: list[str] | None = None, force_rescan: bool = False) -> dict[str, list[dict]]:
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
        with _mem_lock:
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
        elif flag_type == "string":
            return self._write_std_string(abs_addr, "" if value is None else str(value))
        else:
            return False, f"Unsupported type for memory write: {flag_type}"

        return self._write_raw(abs_addr, data)

    def _write_raw(self, abs_addr, data):
        """Locking wrapper — every process write is serialized so two threads
        can't run VirtualProtectEx/WriteProcessMemory on the same page at once
        (the preset-switch corruption/crash). See _mem_lock."""
        with _mem_lock:
            return self._write_raw_impl(abs_addr, data)

    def _write_raw_impl(self, abs_addr, data):
        """Write raw bytes at an absolute address: NtWrite first, then
        VirtualProtectEx + WriteProcessMemory, then classify the page on
        failure. Shared by the numeric and std::string write paths."""
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

    def _write_std_string(self, abs_addr, value):
        """Set an MSVC std::string (x64) value at abs_addr.

        Layout (32 bytes): [ _Bx: 16 ][ _Mysize: 8 ][ _Myres: 8 ].
        MSVC keeps the text inline in _Bx (Small String Optimization) while the
        capacity (_Myres) is < 16; once it grows past that, _Bx[0:8] becomes a
        heap pointer. We honour both so the engine reads the correct length and
        we never overrun the 32-byte object. The whole object is written in one
        shot to avoid a torn intermediate state the engine could read.

        Note: old heap allocations (and our VirtualAllocEx buffers) are
        intentionally leaked — these flags are set rarely and Roblox is about to
        be relaunched anyway, so reclaiming them isn't worth the complexity.
        """
        try:
            raw = value.encode("utf-8")
        except Exception:
            return False, f"Invalid string: {value!r}"
        n = len(raw)
        SSO_CAP = 15  # _BUF_SIZE(16) - 1

        if n <= SSO_CAP:
            # Inline: 16-byte buffer (NUL-padded) + size + capacity(15).
            obj = raw + b"\x00" * (16 - n)
            obj += struct.pack("<Q", n)
            obj += struct.pack("<Q", SSO_CAP)
            return self._write_raw(abs_addr, obj)

        # Heap branch (value > 15 bytes): a safe LIVE write is not possible, so
        # we refuse it and let the JSON path carry the flag at next launch.
        #
        # A heap-backed std::string means pointing _Ptr at a buffer WE allocated
        # (VirtualAllocEx) and setting capacity >= 16. Two crash hazards follow,
        # both unique to long strings (this is why changing a long string flag
        # in-game crashes while ints/bools/SSO strings are fine):
        #   1. Foreign-allocator free. When the engine later destroys or
        #      reassigns this std::string — a DFString refreshed from the server,
        #      or teardown on exit — MSVC calls operator delete on _Ptr. Our
        #      VirtualAllocEx page was never CRT-allocated, so freeing it
        #      corrupts the heap -> crash.
        #   2. Torn cross-field write. The 32-byte object (ptr | size | capacity)
        #      is not written atomically vs the engine's concurrent reads; a read
        #      that sees the new pointer with the old size dereferences out of
        #      bounds -> crash. (A torn scalar is just a wrong number, harmless.)
        #
        # SSO strings (<= 15 bytes, handled above) have neither problem: the text
        # lives inline, there is no pointer to free and nothing to tear. The JSON
        # path applies the long value at engine startup, where it is read safely.
        return False, (
            f"JSON_ONLY|string too long for a safe live write "
            f"(>{SSO_CAP} bytes); applied via JSON at next launch (0x{abs_addr:X})"
        )

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

        # Roblox must be told what to do. '-app' opens the desktop app to its
        # home screen — the same flag the official Roblox shortcut uses.
        # Launching the bare exe with no arguments gives it neither an app mode
        # nor a join ticket, so it just exits immediately (that's why the button
        # appeared to "do nothing"). A real game join still comes through the
        # roblox-player:// protocol path (launch_specific_version with the URI).
        cmdline = f'"{exe_path}" -app'
        success = _k32.CreateProcessW(
            exe_path, cmdline, None, None, False,
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
        log(f"[-] Could not start Roblox: {RobloxManager._launch_error_reason(err)} (err {err})", (255, 100, 100))
        return False, 0, 0, 0

    def launch_specific_version(self, guid, args=None):
        """Launch a specific installed build's RobloxPlayerBeta.exe directly,
        bypassing the auto-updater. `args` is an optional launch/command line
        (e.g. a roblox-player:// join string). Returns (ok, pid)."""
        exe_path = RobloxManager.resolve_version_exe(guid)
        if not exe_path:
            log(f"[-] Build not installed: {guid}", (255, 100, 100))
            return False, 0
        work_dir = os.path.dirname(exe_path)
        cmdline = None
        if args:
            cmdline = f'"{exe_path}" {args}'
        log(f"[*] Launching pinned Roblox build {guid}...", (100, 255, 255))
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        pi = PROCESS_INFORMATION()
        success = _k32.CreateProcessW(
            exe_path, cmdline, None, None, False,
            0, None, work_dir, ctypes.byref(si), ctypes.byref(pi)
        )
        if success:
            log(f"[+] Roblox launched (PID {pi.dwProcessId})", (100, 255, 100))
            _k32.CloseHandle(pi.hThread)
            _k32.CloseHandle(pi.hProcess)
            self.pid = pi.dwProcessId
            return True, pi.dwProcessId
        err = ctypes.get_last_error()
        log(f"[-] Could not start Roblox: {RobloxManager._launch_error_reason(err)} (err {err})", (255, 100, 100))
        return False, 0

    def lock_dynamic_flags(self, target_addrs: list) -> int:
        """Lock flag values against Roblox self-revert by patching their heap-
        object attribute byte to 0x01.

        Design (safe + fast):
          - SINGLE-THREADED. Parallel workers caused UI stalls/crashes because
            all workers pinned the GIL and starved the WebView2 main thread.
          - HARD TIME BUDGET (default 5 s). If Roblox has huge heap arenas we
            stop before we hurt the user. Missed targets are recovered by the
            silent verify loop and by the Turbo watchdog.
          - REGION SIZE CAP (256 MB). Skip GC arenas — they hold random object
            data that yields few real hits per byte scanned, and eat scan time.
          - PER-REGION CHUNK CAP (32 chunks × 2 MB = 64 MB). Guarantees we don't
            sit on one huge region forever.
          - MICRO-LOCK ONLY. Outer scan is lock-free; the 1-byte NtWrite takes
            `_mem_lock` for microseconds (Phase 1 kept).
          - PER-CHUNK EXCEPTION GUARD. If Roblox dies mid-scan, we exit cleanly.
          - YIELDS. `time.sleep(0)` every chunk so the WebView2 thread runs.

        Returns count of unique targets whose attribute byte was flipped 0x00
        -> 0x01. Best-effort; failure never surfaces to the user."""
        if not target_addrs:
            return 0
        if not self._h_process:
            # Entry guard: the previous "Scheduling lock for N…" line promised
            # a result. If we can't run the scan at all (no process handle),
            # close the loop with an explicit line instead of silence — matches
            # the never-silent contract of the summary block below.
            log(f"[!] Lock: 0/{len(target_addrs)} — not attached to Roblox, skipped",
                (200, 180, 100))
            return 0

        TIME_BUDGET_SEC = 5.0
        CHUNK = 2 * 1024 * 1024                    # 2 MB per read
        PAGE = 0x1000
        MIN_REGION = 64 * 1024                     # skip fragmentation noise
        MAX_REGION = 256 * 1024 * 1024             # skip huge GC arenas
        MAX_CHUNKS_PER_REGION = 32                 # 64 MB per region cap
        # Writable-protect masks: PAGE_READWRITE=0x04, PAGE_WRITECOPY=0x08,
        # PAGE_EXECUTE_READWRITE=0x40, PAGE_EXECUTE_WRITECOPY=0x80.
        WRITABLE = {0x04, 0x08, 0x40, 0x80}

        target_set = set(target_addrs)
        locked = set()             # attrs newly flipped 0x00 -> 0x01 this run
        already_locked = set()     # attrs found already at 0x01 from earlier run
        found_ptrs = 0
        t_start = time.time()
        deadline = t_start + TIME_BUDGET_SEC

        # Per-PID cache of attribute-byte addresses we've locked. Silent verify
        # loop re-patches these if Roblox clears them.
        if getattr(self, '_locked_attrs_pid', None) != self.pid:
            self._locked_attrs = set()
            self._locked_attrs_pid = self.pid

        # Per-PID cache of value pointers we've SUCCESSFULLY located this session
        # — either newly-patched or found already at 0x01. A Flags-off/on cycle
        # does NOT touch the heap-object attribute byte (killswitch only reverts
        # the value at the static address), so the lock state carried over from
        # before the pause. Re-scanning would waste the 5s time budget hunting
        # for pointers we already know the location of. Fully-cached call skips
        # the region walk entirely; a partial hit still runs the full scan (some
        # redundant work matching the cached pointers, but the loop exits when
        # everything's covered).
        if getattr(self, '_locked_value_ptrs_pid', None) != self.pid:
            self._locked_value_ptrs = set()
            self._locked_value_ptrs_pid = self.pid

        if target_set <= self._locked_value_ptrs:
            log(f"[+] Lock: {len(target_set)}/{len(target_set)} already locked "
                f"from a prior scan — skipped",
                (100, 200, 255))
            return 0

        cursor = 0x10000
        max_addr = 1 << 47
        # None = clean run. 'timeout' = deadline fired. 'process_gone' = Roblox
        # died mid-scan. Both split out so the log tells the user which happened
        # instead of collapsing to a single "(time budget hit)".
        aborted_reason = None

        while cursor < max_addr and (len(locked) + len(already_locked)) < len(target_set):
            if time.time() > deadline:
                aborted_reason = 'timeout'
                break
            if not self.is_attached:
                aborted_reason = 'process_gone'
                break

            mbi = MEMORY_BASIC_INFORMATION()
            try:
                got = _k32.VirtualQueryEx(
                    self._h_process, ctypes.c_void_p(cursor),
                    ctypes.byref(mbi), ctypes.sizeof(mbi)
                )
            except Exception:
                break
            if not got:
                # Past the highest mapped address.
                break

            region_base = mbi.BaseAddress or cursor
            region_size = mbi.RegionSize or PAGE
            next_cursor = region_base + region_size

            if (mbi.State != 0x1000                     # need MEM_COMMIT
                    or mbi.Type != 0x20000              # need MEM_PRIVATE
                    or (mbi.Protect & 0x100)            # skip PAGE_GUARD
                    or (mbi.Protect & 0xFF) not in WRITABLE
                    or region_size < MIN_REGION
                    or region_size > MAX_REGION):
                cursor = next_cursor
                continue

            offset = 0
            chunks_read = 0
            while (offset < region_size
                    and chunks_read < MAX_CHUNKS_PER_REGION
                    and (len(locked) + len(already_locked)) < len(target_set)):
                if time.time() > deadline:
                    aborted_reason = 'timeout'
                    break
                if not self.is_attached:
                    aborted_reason = 'process_gone'
                    break

                chunk_start = region_base + offset
                chunk_len = min(CHUNK, region_size - offset)

                try:
                    buf = ctypes.create_string_buffer(chunk_len)
                    bytes_read = ctypes.c_size_t(0)
                    _ntdll.NtReadVirtualMemory(
                        self._h_process, ctypes.c_void_p(chunk_start),
                        buf, ctypes.c_size_t(chunk_len), ctypes.byref(bytes_read)
                    )
                    readable = bytes_read.value
                except Exception:
                    # Roblox likely died mid-read; abort cleanly.
                    aborted_reason = 'process_gone'
                    break

                if readable < 8:
                    offset += chunk_len
                    chunks_read += 1
                    continue

                data = buf.raw[:readable]

                # Bulk-unpack qwords with array.array. C-level unpack of the
                # entire chunk in one go, instead of struct.unpack_from per
                # qword in a Python loop. ~5-10x faster on the inner scan.
                qwords = array.array('Q')
                aligned_len = (readable // 8) * 8
                qwords.frombytes(data[:aligned_len])

                for i, val in enumerate(qwords):
                    if val in target_set:
                        found_ptrs += 1
                        byte_off = i * 8
                        attr_addr = chunk_start + byte_off - 0x10
                        if attr_addr <= 0x10000:
                            continue

                        # Attribute byte lives at (pointer - 0x10). If that
                        # offset is inside the CURRENT buffer, read it inline
                        # — no second NtRead syscall. Saves ~300 syscalls per
                        # scan on a real workload. Fallback: syscall only for
                        # the edge case where 0x10 lands before buffer start.
                        rel = byte_off - 0x10
                        if rel >= 0:
                            current_attr = data[rel]
                        else:
                            try:
                                attr_buf = ctypes.create_string_buffer(1)
                                ar = ctypes.c_size_t(0)
                                _ntdll.NtReadVirtualMemory(
                                    self._h_process,
                                    ctypes.c_void_p(attr_addr),
                                    attr_buf, ctypes.c_size_t(1),
                                    ctypes.byref(ar)
                                )
                                if ar.value != 1:
                                    continue
                                current_attr = attr_buf.raw[0]
                            except Exception:
                                continue

                        if current_attr == 0x01:
                            # Already locked from a previous scan — record so
                            # the log honestly reports coverage (was "0/N"
                            # noise before this fix), and cache the value ptr
                            # so the NEXT call can smart-skip if every target
                            # is already known.
                            already_locked.add(val)
                            self._locked_attrs.add(attr_addr)
                            self._locked_value_ptrs.add(val)
                            continue

                        try:
                            one = ctypes.create_string_buffer(b'\x01', 1)
                            wr = ctypes.c_size_t(0)
                            with _mem_lock:  # Phase 1: micro-lock only
                                _ntdll.NtWriteVirtualMemory(
                                    self._h_process, ctypes.c_void_p(attr_addr),
                                    one, ctypes.c_size_t(1), ctypes.byref(wr)
                                )
                            if wr.value == 1:
                                locked.add(val)
                                self._locked_attrs.add(attr_addr)
                                self._locked_value_ptrs.add(val)
                        except Exception:
                            pass

                offset += chunk_len
                chunks_read += 1
                # Yield to WebView2 / watchdog / hotkey threads so the UI stays
                # responsive during the scan.
                time.sleep(0)

            cursor = next_cursor

        elapsed = time.time() - t_start
        newly = len(locked)
        prev = len(already_locked - locked)
        covered = len(locked | already_locked)
        missing = len(target_set) - covered
        # Always emit a closing line so the earlier "Scheduling lock for N…"
        # never dangles without follow-up. Even the "found nothing" case gets a
        # diagnostic so the user knows the scan finished, not silently died.
        if aborted_reason == 'timeout' and missing > 0:
            tag = " (time budget hit)"
        elif aborted_reason == 'process_gone':
            tag = " (process gone)"
        elif covered == 0 and found_ptrs == 0:
            # Scan finished cleanly but found none of the target pointers in
            # scanned regions. Usually means Roblox hasn't allocated the flag
            # objects yet (early launch) or the address list is stale.
            tag = " (no matching pointers — flags not yet in heap)"
        else:
            tag = ""
        # Colour: teal on any coverage, muted amber on a zero-coverage or
        # aborted scan so the user's eye catches the diagnostic.
        colour = (100, 200, 255) if covered > 0 else (200, 180, 100)
        log(f"[+] Lock: {covered}/{len(target_set)} covered in {elapsed:.1f}s "
            f"({newly} new patches, {prev} already locked, "
            f"{missing} not yet in heap){tag}",
            colour)

        # Start silent verify+re-lock thread once per RobloxManager instance.
        # Re-reads each locked attribute byte periodically and re-writes 0x01
        # if Roblox reset it. No logging — silent by user request.
        if not getattr(self, '_verify_lock_running', False):
            self._verify_lock_running = True
            threading.Thread(target=self._verify_locks_loop, daemon=True).start()

        return newly

    def _verify_locks_loop(self):
        """Verify+re-lock tick. Every 30 s, re-read each locked attribute byte;
        if Roblox reset it back to 0x00, write 0x01 again.

        Silent unless Roblox actually clobbered something — then one line per
        tick reporting the count of re-locks. That way steady-state stays quiet
        but a suddenly-noisy game (heap moves, GC passes wiping attribute bytes)
        is visible instead of invisible."""
        while getattr(self, '_verify_lock_running', False):
            time.sleep(30.0)
            relocked = 0
            try:
                if not self.is_attached or not self._h_process:
                    continue
                # Stale-PID guard: don't touch attr addresses from a dead Roblox.
                if getattr(self, '_locked_attrs_pid', None) != self.pid:
                    continue
                addrs = list(getattr(self, '_locked_attrs', ()))
                if not addrs:
                    continue
                with _mem_lock:
                    for attr_addr in addrs:
                        try:
                            attr_buf = ctypes.create_string_buffer(1)
                            ar = ctypes.c_size_t(0)
                            _ntdll.NtReadVirtualMemory(
                                self._h_process, ctypes.c_void_p(attr_addr),
                                attr_buf, ctypes.c_size_t(1), ctypes.byref(ar)
                            )
                            if ar.value == 1 and attr_buf.raw[0:1] != b'\x01':
                                one = ctypes.create_string_buffer(b'\x01', 1)
                                wr = ctypes.c_size_t(0)
                                _ntdll.NtWriteVirtualMemory(
                                    self._h_process, ctypes.c_void_p(attr_addr),
                                    one, ctypes.c_size_t(1), ctypes.byref(wr)
                                )
                                if wr.value == 1:
                                    relocked += 1
                        except Exception:
                            pass
            except Exception:
                pass
            if relocked > 0:
                log(f"[+] Watchdog: re-locked {relocked} flag(s) Roblox reset",
                    (180, 200, 100))

