"""Tests for RobloxManager._write_std_string.

Long (heap-backed) std::string values must NOT be poked into live memory:
pointing the string at our own VirtualAllocEx buffer crashes the game when the
engine later frees/reassigns it, or on a torn cross-field read. Short (SSO)
strings stay inline and are safe to write live.
"""
import struct

from src.core.roblox_manager import RobloxManager


def _mgr(monkeypatch):
    m = RobloxManager.__new__(RobloxManager)  # bypass heavy __init__
    calls = []

    def fake_write_raw(abs_addr, data):
        calls.append((abs_addr, data))
        return True, f"OK|test (0x{abs_addr:X})"

    monkeypatch.setattr(m, "_write_raw", fake_write_raw)
    return m, calls


def test_short_string_writes_inline_sso(monkeypatch):
    m, calls = _mgr(monkeypatch)
    ok, msg = m._write_std_string(0x1000, "rxLatency")  # 9 bytes -> SSO
    assert ok is True
    assert len(calls) == 1
    addr, data = calls[0]
    assert addr == 0x1000
    assert len(data) == 32                       # full std::string object
    assert data[:9] == b"rxLatency"
    assert struct.unpack("<Q", data[16:24])[0] == 9    # _Mysize
    assert struct.unpack("<Q", data[24:32])[0] == 15   # _Myres (SSO cap)


def test_empty_string_is_sso(monkeypatch):
    m, calls = _mgr(monkeypatch)
    ok, _ = m._write_std_string(0x1000, "")
    assert ok is True
    assert len(calls) == 1
    assert struct.unpack("<Q", calls[0][1][16:24])[0] == 0


def test_long_string_is_json_only_and_never_writes_memory(monkeypatch):
    m, calls = _mgr(monkeypatch)
    # "ExponentialDecay" is exactly 16 bytes -> heap branch -> must be refused.
    ok, msg = m._write_std_string(0x2000, "ExponentialDecay")
    assert ok is False
    assert msg.startswith("JSON_ONLY")
    assert calls == []   # crucially, no live memory write happened


def test_sso_boundary_15_vs_16(monkeypatch):
    m, calls = _mgr(monkeypatch)
    ok15, _ = m._write_std_string(0x10, "x" * 15)   # 15 -> still SSO
    ok16, msg16 = m._write_std_string(0x10, "x" * 16)  # 16 -> JSON_ONLY
    assert ok15 is True
    assert ok16 is False and msg16.startswith("JSON_ONLY")
    # Only the 15-byte write reached memory.
    assert len(calls) == 1


def test_multibyte_counts_utf8_bytes_not_chars(monkeypatch):
    m, calls = _mgr(monkeypatch)
    # 8 multibyte chars = 16 UTF-8 bytes -> heap branch -> JSON_ONLY.
    ok, msg = m._write_std_string(0x30, "é" * 8)
    assert ok is False and msg.startswith("JSON_ONLY")
    assert calls == []
