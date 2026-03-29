import sys
import os
import struct
import ctypes
import ctypes.wintypes as wintypes

# Add src to path so we can import our modules
sys.path.append(os.getcwd())

from src.core.roblox_manager import RobloxManager
from src.core.scanner import FFlagScanner

def main():
    rm = RobloxManager()
    print("[*] Searching for Roblox process...")
    pid = rm.find_roblox_process()
    if not pid:
        print("[-] Roblox is not running.")
        return

    print(f"[+] Found Roblox (PID: {pid}). Opening read-only handle...")
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    
    if not handle:
        print(f"[-] Failed to open process.")
        return
        
    rm._h_process = handle
    base = rm.get_roblox_base()
    scanner = FFlagScanner(rm._h_process, base)
    bank_ptr = scanner.scan_for_fflag_bank()
    if not bank_ptr:
        return

    buckets_ptr = scanner.read_ptr(bank_ptr + 0x18)
    bucket_mask = scanner.read_ptr(bank_ptr + 0x30)
    bucket_count = bucket_mask + 1
    
    vtable_groups = {} # vtable_offset -> [names]

    print(f"[*] Scanning {bucket_count} buckets for VTable clusters (500 samples)...")

    scanned_count = 0
    for i in range(bucket_count):
        curr = scanner.read_ptr(buckets_ptr + (i * 0x10))
        last_node = scanner.read_ptr(buckets_ptr + (i * 0x10) + 0x8)
        
        while curr and curr != last_node:
            length = scanner.read_ptr(curr + 0x20)
            if 0 < length < 100:
                str_addr = curr + 0x10
                if length > 15: str_addr = scanner.read_ptr(str_addr)
                
                raw_name = scanner.read_mem(str_addr, length)
                if raw_name:
                    name = raw_name.decode('utf-8', errors='ignore')
                    getset_ptr = scanner.read_ptr(curr + 0x30)
                    if getset_ptr:
                        vtable = scanner.read_ptr(getset_ptr)
                        v_offset = vtable - base
                        if v_offset not in vtable_groups:
                            vtable_groups[v_offset] = []
                        vtable_groups[v_offset].append(name)
                        scanned_count += 1
            
            curr = scanner.read_ptr(curr + 0x8)
            if scanned_count >= 500: break
        if scanned_count >= 500: break

    print("\n=== VTable Type Clusters Found ===")
    for v_offset, names in sorted(vtable_groups.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"\nVTable Offset: 0x{v_offset:X}")
        print(f"Count: {len(names)}")
        # Print first 15 samples to identify the group
        print(f"Samples: {', '.join(names[:15])}...")

if __name__ == "__main__":
    main()
