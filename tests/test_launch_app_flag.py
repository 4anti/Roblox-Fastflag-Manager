"""The plain 'Launch Roblox' button must pass '-app' so Roblox opens to its home
screen instead of being launched bare (no ticket/no app mode), which makes modern
Roblox exit immediately. See the launch root-cause fix (2026-06-23)."""
from src.core import roblox_manager as rm_mod
from src.core.roblox_manager import RobloxManager
from src.core.version_changer import channel, deployment, fixer


def test_launch_and_patch_passes_app_flag(tmp_path, monkeypatch):
    vdir = tmp_path / "version-abc"
    vdir.mkdir()
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"x")
    monkeypatch.setattr(
        RobloxManager, "get_roblox_version_dir", staticmethod(lambda: str(vdir))
    )
    monkeypatch.setattr(deployment, "get_latest_production_guid", lambda: None)
    monkeypatch.setattr(RobloxManager, "is_roblox_running", staticmethod(lambda: False))
    monkeypatch.setattr(fixer, "prune_stock_non_production",
                        lambda *_a, **_k: {"removed": [], "failed": [], "kept": None})
    monkeypatch.setattr(channel, "pin_production_channel", lambda: True)

    captured = {}

    def fake_create_process(app_name, cmdline, *args, **kwargs):
        captured["app_name"] = app_name
        captured["cmdline"] = cmdline
        return 1  # non-zero = success

    monkeypatch.setattr(rm_mod._k32, "CreateProcessW", fake_create_process)

    rm = RobloxManager()
    ok, _pid, _, _ = rm.launch_and_patch_roblox([])

    assert ok is True
    assert captured["app_name"].endswith("RobloxPlayerBeta.exe")
    # The command line must request app/home mode.
    assert "-app" in captured["cmdline"]
    # And it must still reference the executable (argv[0]).
    assert "RobloxPlayerBeta.exe" in captured["cmdline"]


def test_launch_and_patch_prefers_production_folder(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    stock = local / "Roblox" / "Versions"
    leftover = stock / "version-7d4de67b1ae241a2"
    production = stock / "version-ddf602d9cfe44005"
    leftover.mkdir(parents=True)
    production.mkdir(parents=True)
    (leftover / "RobloxPlayerBeta.exe").write_bytes(b"old")
    (production / "RobloxPlayerBeta.exe").write_bytes(b"new")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: None))
    monkeypatch.setattr(deployment, "get_latest_production_guid",
                        lambda: "version-ddf602d9cfe44005")
    monkeypatch.setattr(RobloxManager, "is_roblox_running", staticmethod(lambda: False))
    monkeypatch.setattr(fixer, "prune_stock_non_production",
                        lambda *_a, **_k: {"removed": [], "failed": [], "kept": None})
    monkeypatch.setattr(channel, "pin_production_channel", lambda: True)

    captured = {}

    def fake_create_process(app_name, cmdline, *args, **kwargs):
        captured["app_name"] = app_name
        captured["cmdline"] = cmdline
        return 1

    monkeypatch.setattr(rm_mod._k32, "CreateProcessW", fake_create_process)

    rm = RobloxManager()
    ok, _pid, _, _ = rm.launch_and_patch_roblox([])

    assert ok is True
    assert captured["app_name"] == str(production / "RobloxPlayerBeta.exe")


def _stub_launch_dir(tmp_path, monkeypatch):
    vdir = tmp_path / "version-abc"
    vdir.mkdir()
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"x")
    monkeypatch.setattr(
        RobloxManager, "get_roblox_version_dir", staticmethod(lambda: str(vdir))
    )
    monkeypatch.setattr(deployment, "get_latest_production_guid", lambda: None)
    monkeypatch.setattr(RobloxManager, "is_roblox_running", staticmethod(lambda: False))
    monkeypatch.setattr(fixer, "prune_stock_non_production",
                        lambda *_a, **_k: {"removed": [], "failed": [], "kept": None})
    captured = {}

    def fake_create_process(app_name, cmdline, *args, **kwargs):
        captured["app_name"] = app_name
        captured["cmdline"] = cmdline
        return 1

    monkeypatch.setattr(rm_mod._k32, "CreateProcessW", fake_create_process)
    return captured


def test_launch_and_patch_pins_production_channel(tmp_path, monkeypatch):
    captured = _stub_launch_dir(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(channel, "pin_production_channel",
                        lambda: called.append(True) or True)

    ok, _pid, _, _ = RobloxManager().launch_and_patch_roblox([])

    assert ok is True
    assert called == [True]
    assert "-app" in captured["cmdline"]


def test_launch_and_patch_continues_if_pin_fails(tmp_path, monkeypatch):
    captured = _stub_launch_dir(tmp_path, monkeypatch)

    def boom(_value):
        raise OSError("access denied")

    monkeypatch.setattr(channel, "_write_player_channel", boom)

    ok, _pid, _, _ = RobloxManager().launch_and_patch_roblox([])

    assert ok is True
    assert "-app" in captured["cmdline"]

