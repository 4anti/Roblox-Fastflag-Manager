"""The plain 'Launch Roblox' button must pass '-app' so Roblox opens to its home
screen instead of being launched bare (no ticket/no app mode), which makes modern
Roblox exit immediately. See the launch root-cause fix (2026-06-23)."""
from src.core import roblox_manager as rm_mod
from src.core.roblox_manager import RobloxManager


def test_launch_and_patch_passes_app_flag(tmp_path, monkeypatch):
    vdir = tmp_path / "version-abc"
    vdir.mkdir()
    (vdir / "RobloxPlayerBeta.exe").write_bytes(b"x")
    monkeypatch.setattr(
        RobloxManager, "get_roblox_version_dir", staticmethod(lambda: str(vdir))
    )

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
