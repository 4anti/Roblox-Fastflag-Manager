import ctypes
from ctypes import wintypes
import sys

# Constants
PAGE_EXECUTE_READWRITE = 0x40
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000

class SyscallManager:
    def __init__(self):
        self.ntdll = ctypes.windll.ntdll
        self.kernel32 = ctypes.windll.kernel32
        
        self.ssn_protect = self.get_ssn("NtProtectVirtualMemory")
        self.ssn_write = self.get_ssn("NtWriteVirtualMemory")
        self.ssn_read = self.get_ssn("NtReadVirtualMemory")
        self.ssn_suspend = self.get_ssn("NtSuspendProcess")
        self.ssn_resume = self.get_ssn("NtResumeProcess")
        
        print(f"[*] SSNs: Protect={self.ssn_protect}, Write={self.ssn_write}, Read={self.ssn_read}")
        
        if not all([self.ssn_protect, self.ssn_write, self.ssn_read, self.ssn_suspend, self.ssn_resume]):
            raise Exception("Failed to resolve SSNs")

        self.stub_memory = self.allocate_stub_memory()
        self.protect_stub = self.create_syscall_stub(self.ssn_protect, 0)
        self.write_stub = self.create_syscall_stub(self.ssn_write, 32) # Offset for next stub
        self.read_stub = self.create_syscall_stub(self.ssn_read, 64)
        self.suspend_stub = self.create_syscall_stub(self.ssn_suspend, 96)
        self.resume_stub = self.create_syscall_stub(self.ssn_resume, 128)

    def get_ssn(self, func_name):
        # Configure GetProcAddress to return c_void_p to handle 64-bit addresses correctly
        self.kernel32.GetProcAddress.restype = ctypes.c_void_p
        self.kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
        
        func_addr = self.kernel32.GetProcAddress(self.ntdll._handle, func_name.encode('utf-8'))
        if not func_addr: return None
        
        # Read byte pattern
        # cast to pointer
        byte_ptr = ctypes.cast(func_addr, ctypes.POINTER(ctypes.c_ubyte * 16))
        
        try:
            bytes_read = list(byte_ptr.contents)
        except ValueError:
            return None
        
        # Check for mov r10, rcx; mov eax, SSN
        if bytes_read[0] == 0x4C and bytes_read[1] == 0x8B and bytes_read[2] == 0xD1 and bytes_read[3] == 0xB8:
            return bytes_read[4] | (bytes_read[5] << 8)
        return None

    def allocate_stub_memory(self):
        # Configure VirtualAlloc
        self.kernel32.VirtualAlloc.restype = ctypes.c_void_p
        self.kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.c_ulong]
        
        # Allocate executable memory for stubs
        # 0x1000 = 4KB
        ptr = self.kernel32.VirtualAlloc(None, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
        if not ptr:
            raise Exception(f"Failed to allocate stub memory: {ctypes.GetLastError()}")
        return ptr

    def create_syscall_stub(self, ssn, offset):
        address = self.stub_memory + offset
        
        # x64 Syscall Stub:
        # mov r10, rcx
        # mov eax, <SSN>
        # syscall
        # ret
        
        code = [
            0x4C, 0x8B, 0xD1,               # mov r10, rcx
            0xB8, ssn & 0xFF, (ssn >> 8) & 0xFF, 0x00, 0x00, # mov eax, ssn
            0x0F, 0x05,                     # syscall
            0xC3                            # ret
        ]
        
        # Write code to memory
        byte_array = (ctypes.c_ubyte * len(code))(*code)
        ctypes.memmove(ctypes.c_void_p(address), byte_array, len(code))
        return address

    def nt_protect_virtual_memory(self, process_handle, base_address, size, new_protect):
        # NtProtectVirtualMemory(ProcessHandle, *BaseAddress, *NumberOfBytesToProtect, NewAccessProtection, *OldAccessProtection)
        
        # Define function prototype
        Prototype = ctypes.CFUNCTYPE(
            ctypes.c_long, # NTSTATUS
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong)
        )
        
        func = Prototype(self.protect_stub)
        
        # Prepare arguments by reference
        p_base_addr = ctypes.c_void_p(base_address)
        p_size = ctypes.c_size_t(size)
        old_protect = ctypes.c_ulong()
        
        status = func(process_handle, ctypes.byref(p_base_addr), ctypes.byref(p_size), new_protect, ctypes.byref(old_protect))
        return status == 0, old_protect.value

    def nt_write_virtual_memory(self, process_handle, base_address, buffer, size):
        # NtWriteVirtualMemory(ProcessHandle, BaseAddress, Buffer, NumberOfBytesToWrite, *NumberOfBytesWritten)
        
        Prototype = ctypes.CFUNCTYPE(
            ctypes.c_long,
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t)
        )
        
        func = Prototype(self.write_stub)
        
        written = ctypes.c_size_t()
        
        status = func(process_handle, ctypes.c_void_p(base_address), buffer, size, ctypes.byref(written))
        return status == 0

    def nt_read_virtual_memory(self, process_handle, base_address, buffer, size):
        # NtReadVirtualMemory(ProcessHandle, BaseAddress, Buffer, NumberOfBytesToRead, *NumberOfBytesRead)
        
        Prototype = ctypes.CFUNCTYPE(
            ctypes.c_long,
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t)
        )
        
        func = Prototype(self.read_stub)
        
        read = ctypes.c_size_t()
        
        status = func(process_handle, ctypes.c_void_p(base_address), buffer, size, ctypes.byref(read))
        return status == 0, read.value

    def nt_suspend_process(self, process_handle):
        # NtSuspendProcess(ProcessHandle)
        Prototype = ctypes.CFUNCTYPE(ctypes.c_long, wintypes.HANDLE)
        func = Prototype(self.suspend_stub)
        status = func(process_handle)
        return status == 0

    def nt_resume_process(self, process_handle):
        # NtResumeProcess(ProcessHandle)
        Prototype = ctypes.CFUNCTYPE(ctypes.c_long, wintypes.HANDLE)
        func = Prototype(self.resume_stub)
        status = func(process_handle)
        return status == 0
