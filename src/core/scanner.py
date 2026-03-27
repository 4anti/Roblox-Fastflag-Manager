import os
import json
import time
import struct
import ctypes
import ctypes.wintypes as wintypes
from src.utils.logger import log

# We use the same NTDLL defined in roblox_manager or we can redefine it here
_ntdll = ctypes.WinDLL('ntdll', use_last_error=True)

_ntdll.NtReadVirtualMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
_ntdll.NtReadVirtualMemory.restype = ctypes.c_long

def get_cache_file(version):
    local = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(local, "Roblox", f"fflags_offsets_{version}.json")

class FFlagScanner:
    def __init__(self, h_process, base_address):
        self.h_process = h_process
        self.base_address = base_address
        self.sections = {}

    def read_mem(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        status = _ntdll.NtReadVirtualMemory(
            self.h_process, ctypes.c_void_p(addr),
            buf, ctypes.c_size_t(size), ctypes.byref(bytes_read)
        )
        if status == 0 and bytes_read.value > 0:
            return buf.raw[:bytes_read.value]
        return None

    def read_ptr(self, addr):
        data = self.read_mem(addr, 8)
        if data and len(data) == 8:
            return struct.unpack("<Q", data)[0]
        return 0

    def parse_pe_headers(self):
        # Read DOS header
        dos = self.read_mem(self.base_address, 64)
        if not dos or dos[0:2] != b"MZ":
            return False
            
        e_lfanew = struct.unpack_from("<I", dos, 0x3C)[0]
        
        # Read NT Headers
        nt = self.read_mem(self.base_address + e_lfanew, 24)
        if not nt or nt[0:4] != b"PE\x00\x00":
            return False
            
        num_sections = struct.unpack_from("<H", nt, 6)[0]
        optional_header_size = struct.unpack_from("<H", nt, 20)[0]
        
        # Parse sections
        sec_offset = self.base_address + e_lfanew + 24 + optional_header_size
        sec_data = self.read_mem(sec_offset, num_sections * 40)
        if not sec_data:
            return False
            
        for i in range(num_sections):
            sec = sec_data[i*40 : (i+1)*40]
            name = sec[0:8].rstrip(b'\x00').decode('utf-8', errors='ignore')
            v_size = struct.unpack_from("<I", sec, 8)[0]
            v_addr = struct.unpack_from("<I", sec, 12)[0]
            self.sections[name] = {"addr": self.base_address + v_addr, "size": v_size}
            
        return True

    def scan_for_fflag_bank(self):
        log("[*] [Local Scanner] Parsing PE headers...", (200, 200, 100))
        if not self.parse_pe_headers():
            log("[-] [Local Scanner] Failed to parse PE headers.", (255, 100, 100))
            return 0
            
        target_str = b"DebugSkyGray"
        target_addrs = []
        
        # Scan ALL sections for the string
        for sec_name, sec_info in self.sections.items():
            sec_addr = sec_info["addr"]
            sec_size = sec_info["size"]
            if sec_size == 0: continue
            
            sec_mem = self.read_mem(sec_addr, sec_size)
            if not sec_mem: continue
            
            idx = 0
            while True:
                idx = sec_mem.find(target_str, idx)
                if idx == -1:
                    break
                target_addrs.append(sec_addr + idx)
                idx += 1
            
        if not target_addrs:
            log("[-] [Local Scanner] Could not find DebugSkyGray string anywhere in PE sections.", (255, 100, 100))
            return 0
            
        log("[*] [Local Scanner] Downloading .text section for xref scan...", (200, 200, 100))
        text = self.sections.get(".text")
        if not text:
            log("[-] [Local Scanner] Missing .text section.", (255, 100, 100))
            return 0
        
        # Define structures for VirtualQueryEx
        _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
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
        
        xrefs = []
        
        # Scan .text section by committed memory regions only
        curr = text["addr"]
        end = text["addr"] + text["size"]
        log(f"[*] Scanning .text from {curr:X} to {end:X}...", (200, 200, 100))
        
        while curr < end:
            mbi = MEMORY_BASIC_INFORMATION()
            res = _k32.VirtualQueryEx(self.h_process, ctypes.c_void_p(curr), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if res == 0:
                log(f"[-] VirtualQueryEx failed at {curr:X}", (255, 100, 100))
                break
                
            region_start = mbi.BaseAddress
            if region_start is None:
                region_start = 0
                
            region_size = mbi.RegionSize
            region_end = region_start + region_size
            
            # Bound region strictly to .text
            if region_start < curr:
                region_size -= (curr - region_start)
                region_start = curr
            if region_end > end:
                region_size = end - region_start
                
            # Avoid PAGE_NOACCESS (0x01) and PAGE_GUARD (0x100)
            if (mbi.State == 0x1000) and not (mbi.Protect & 0x01) and not (mbi.Protect & 0x100):
                region_mem = self.read_mem(region_start, region_size)
                if region_mem:
                    # Scan for xrefs (disp32) in this valid committed region against ALL string instances
                    for i in range(len(region_mem) - 4):
                        disp = struct.unpack_from("<i", region_mem, i)[0]
                        abs_addr = (region_start + i + 4) + disp
                        if abs_addr in target_addrs:
                            xrefs.append(region_start + i)
                            
            curr += mbi.RegionSize
            
        log(f"[*] [Local Scanner] Finished scanning .text from {len(target_addrs)} string occurrences.", (200, 200, 100))
        if not xrefs:
            log("[-] [Local Scanner] No xrefs found for DebugSkyGray.", (255, 100, 100))
            return 0
            
        log(f"[*] [Local Scanner] Found {len(xrefs)} xrefs. Scanning windows...", (200, 200, 100))
        
        # For each xref, look backwards for MOV RCX, [RIP+disp] (48 8B 0D)
        for xref in xrefs:
            window_size = 0x40
            start = xref - window_size if xref > window_size else 0
            
            window = self.read_mem(start, window_size + 4)
            if not window:
                continue
            
            for offset in range(len(window) - 7):
                if window[offset] == 0x48 and window[offset+1] == 0x8B and window[offset+2] == 0x0D:
                    disp = struct.unpack_from("<i", window, offset + 3)[0]
                    rip = start + offset + 7
                    bank_addr = rip + disp
                    log(f"[DEBUG] Found 48 8B 0D at offset {offset}, bank_addr=0x{bank_addr:X}", (200,200,200))
                    if self.base_address <= bank_addr < (self.base_address + 0x10000000): # Sanity bounds
                        log(f"[+] [Local Scanner] Found FFlag Bank at 0x{bank_addr:X}", (100, 255, 100))
                        # The C++ tool does a direct read. We must read the pointer itself:
                        bank_ptr = self.read_ptr(bank_addr)
                        return bank_ptr
            
            # If not found, print a hex dump of the window to see what is there
            dump = " ".join([f"{b:02X}" for b in window])
            log(f"[DEBUG] Window dump around xref 0x{xref:X}:\n{dump}", (200,200,200))
            
        return 0

    def dump_fflags(self, force=False):
        """Scrape all RVAs offline directly from the live/suspended process memory."""
        # Check cache First
        pass  # We will implement cache logic at the manager level

        bank_ptr = self.scan_for_fflag_bank()
        if not bank_ptr:
            return {}
            
        # Dump using unordered map struct
        buckets_ptr = self.read_ptr(bank_ptr + 0x18)
        bucket_mask = self.read_ptr(bank_ptr + 0x30)
        bucket_count = bucket_mask + 1
        
        if bucket_count == 0 or bucket_count > 100000:
            log("[-] [Local Scanner] Invalid bucket count.", (255, 100, 100))
            return {}
            
        log(f"[*] [Local Scanner] Walking {bucket_count} buckets...", (200, 200, 100))
        
        visited = set()
        rva_offset = 0
        fflags = {}
        
        for i in range(bucket_count):
            bucket_addr = buckets_ptr + (i * 0x10)
            first_node = self.read_ptr(bucket_addr)
            last_node = self.read_ptr(bucket_addr + 0x8)
            
            if not first_node or first_node == last_node:
                continue
                
            curr = first_node
            while curr and curr != last_node:
                if curr in visited:
                    break
                visited.add(curr)
                
                length = self.read_ptr(curr + 0x20)
                if not length or length > 1000:
                    curr = self.read_ptr(curr + 0x8)
                    continue
                    
                # Read string
                str_addr = curr + 0x10
                if length > 15:
                    str_addr = self.read_ptr(str_addr)
                    
                raw_str = self.read_mem(str_addr, length)
                if not raw_str:
                    curr = self.read_ptr(curr + 0x8)
                    continue
                    
                try:
                    name = raw_str.decode('utf-8')
                except Exception:
                    curr = self.read_ptr(curr + 0x8)
                    continue
                    
                # Get the RVA
                getset = self.read_ptr(curr + 0x30)
                if getset:
                    # RVA Discovery (only needs to run once)
                    if rva_offset == 0:
                        for off in range(0x8, 0x1000, 0x8):
                            absolute = self.read_ptr(getset + off)
                            if self.base_address <= absolute < (self.base_address + 0x10000000):
                                rva_offset = off
                                break
                    
                    if rva_offset:
                        absolute = self.read_ptr(getset + rva_offset)
                        if absolute:
                            off_from_base = absolute - self.base_address
                            if off_from_base > 0:
                                fflags[name] = hex(off_from_base)
                
                next_node = self.read_ptr(curr + 0x8)
                if not next_node or next_node == first_node:
                    break
                curr = next_node
                
        log(f"[+] [Local Scanner] Successfully dumped {len(fflags)} FFlag RVAs natively!", (100, 255, 100))
        return fflags
