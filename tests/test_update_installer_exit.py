"""Update Now and Auto Update must exit immediately after a successful
installer launch so Setup can replace the running FFM.exe.

ShellExecuteW is mocked; os._exit is mocked so the test process stays alive.
"""
import ctypes

from src.gui import api as api_mod
from src.gui.api import Api
from src.utils import updater


class _StreamResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.headers = {"content-length": str(len(payload))}
        self._payload = payload

    def iter_content(self, chunk_size=65536):
        data = self._payload
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]


class _BodyResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.content = payload


class _ImmediateThread:
    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self):
        self._target()


def _patch_shell(monkeypatch, result=42):
    calls = []

    def fake_shell(*args):
        calls.append(args)
        return result

    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell)
    return calls


def _patch_exit(monkeypatch, module):
    exited = []
    monkeypatch.setattr(module.os, "_exit", lambda code: exited.append(code))
    return exited


def test_launch_helper_exits_on_shell_success(monkeypatch):
    calls = _patch_shell(monkeypatch, result=42)
    exited = _patch_exit(monkeypatch, updater)

    result = updater._launch_installer_and_exit(r"C:\Temp\Setup_FFM_4.1.0.exe")

    assert exited == [0]
    assert result is True
    assert calls, "ShellExecuteW should have been called"
    hwnd, verb, path, args, directory, show = calls[0]
    assert verb == "open"
    assert path.endswith("Setup_FFM_4.1.0.exe")
    assert "/VERYSILENT" in args
    assert "/SUPPRESSMSGBOXES" in args
    assert "/NORESTART" in args
    assert "/FORCECLOSEAPPLICATIONS" in args
    assert "runas" not in (verb, args)


def test_launch_helper_returns_false_on_shell_failure(monkeypatch):
    _patch_shell(monkeypatch, result=2)
    exited = _patch_exit(monkeypatch, updater)

    result = updater._launch_installer_and_exit(r"C:\Temp\Setup_FFM_4.1.0.exe")

    assert exited == []
    assert result is False


def test_download_update_exits_on_launch_success(tmp_path, monkeypatch):
    payload = b"x" * 120000
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _StreamResp(payload))
    calls = _patch_shell(monkeypatch, result=42)
    exited = _patch_exit(monkeypatch, updater)

    result = updater.download_update("https://example.invalid/setup.exe", "4.1.0")

    assert exited == [0]
    assert result is True
    assert "/FORCECLOSEAPPLICATIONS" in calls[0][3]
    assert calls[0][1] == "open"
    written = tmp_path / "Setup_FFM_4.1.0.exe"
    assert written.exists() and written.stat().st_size == len(payload)


def test_download_update_no_exit_on_launch_failure(tmp_path, monkeypatch):
    payload = b"x" * 120000
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _StreamResp(payload))
    _patch_shell(monkeypatch, result=5)
    exited = _patch_exit(monkeypatch, updater)

    result = updater.download_update("https://example.invalid/setup.exe", "4.1.0")

    assert exited == []
    assert result is False


def test_download_update_aborts_tiny_file_without_launch(tmp_path, monkeypatch):
    payload = b"x" * 50
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _StreamResp(payload))
    calls = _patch_shell(monkeypatch, result=42)
    exited = _patch_exit(monkeypatch, updater)

    result = updater.download_update("https://example.invalid/setup.exe", "4.1.0")

    assert result is False
    assert exited == []
    assert calls == []


def test_perform_silent_update_exits_on_launch_success(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _BodyResp(b"installer"))
    calls = _patch_shell(monkeypatch, result=42)
    exited = _patch_exit(monkeypatch, updater)

    result = updater.perform_silent_update("https://example.invalid/setup.exe", "4.1.0")

    assert exited == [0]
    assert result is True
    assert calls[0][1] == "open"
    assert "/FORCECLOSEAPPLICATIONS" in calls[0][3]


def test_perform_silent_update_no_exit_on_launch_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _BodyResp(b"installer"))
    _patch_shell(monkeypatch, result=31)
    exited = _patch_exit(monkeypatch, updater)

    result = updater.perform_silent_update("https://example.invalid/setup.exe", "4.1.0")

    assert exited == []
    assert result is False


def test_trigger_manual_update_exits_without_sleep_on_success(monkeypatch):
    a = Api.__new__(Api)
    a._pending_update = {"exe_url": "https://example.invalid/setup.exe", "version": "4.1.0"}
    a._update_progress = 0

    monkeypatch.setattr(api_mod, "download_update", lambda *a, **k: True)
    slept = []
    monkeypatch.setattr(api_mod.time, "sleep", lambda s: slept.append(s))
    exited = []
    monkeypatch.setattr(api_mod.os, "_exit", lambda code: exited.append(code))
    monkeypatch.setattr(api_mod.threading, "Thread", _ImmediateThread)

    assert a.trigger_manual_update() is True
    assert exited == [0]
    assert slept == []
    assert a._update_progress == 100


def test_trigger_manual_update_stays_alive_on_failure(monkeypatch):
    a = Api.__new__(Api)
    a._pending_update = {"exe_url": "https://example.invalid/setup.exe", "version": "4.1.0"}
    a._update_progress = 0

    monkeypatch.setattr(api_mod, "download_update", lambda *a, **k: False)
    slept = []
    monkeypatch.setattr(api_mod.time, "sleep", lambda s: slept.append(s))
    exited = []
    monkeypatch.setattr(api_mod.os, "_exit", lambda code: exited.append(code))
    monkeypatch.setattr(api_mod.threading, "Thread", _ImmediateThread)

    assert a.trigger_manual_update() is True
    assert exited == []
    assert slept == []
    assert a._update_progress == -1
