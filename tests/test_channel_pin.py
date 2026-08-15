"""Production-channel pin: registry write + join-URI rewrite. Never hits HKCU."""
from src.core.version_changer import channel


def test_rewrite_replaces_non_production_channel():
    uri = (
        "roblox-player:1+launchmode:play+channel:zlive"
        "+placelauncherurl:https%3A%2F%2Fwww.roblox.com%2FGame%2FPlaceLauncher.ashx"
    )
    out = channel.rewrite_launch_args_channel(uri)
    assert "channel:production" in out
    assert "channel:zlive" not in out
    assert "placelauncherurl:https%3A%2F%2Fwww.roblox.com" in out


def test_rewrite_is_case_insensitive():
    uri = "roblox-player:1+channel:LIVE+launchmode:play"
    assert "channel:production" in channel.rewrite_launch_args_channel(uri)
    assert "channel:LIVE" not in channel.rewrite_launch_args_channel(uri)


def test_rewrite_leaves_uri_without_channel_unchanged():
    uri = "roblox-player:1+launchmode:play+placelauncherurl:https%3A%2F%2Fx"
    assert channel.rewrite_launch_args_channel(uri) == uri


def test_rewrite_none_and_empty():
    assert channel.rewrite_launch_args_channel(None) is None
    assert channel.rewrite_launch_args_channel("") == ""


def test_pin_writes_empty_www_roblox_com(monkeypatch):
    written = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Winreg:
        HKEY_CURRENT_USER = object()
        REG_SZ = 1

        def CreateKey(self, hive, path):
            written["hive"] = hive
            written["path"] = path
            return _Key()

        def SetValueEx(self, key, name, reserved, typ, value):
            written["name"] = name
            written["reserved"] = reserved
            written["type"] = typ
            written["value"] = value

    monkeypatch.setattr(channel, "winreg", _Winreg())
    assert channel.pin_production_channel() is True
    assert written["path"] == (
        r"SOFTWARE\ROBLOX Corporation\Environments\RobloxPlayer\Channel"
    )
    assert written["name"] == "www.roblox.com"
    assert written["value"] == ""
    assert written["type"] == 1


def test_pin_never_raises(monkeypatch):
    class _Boom:
        HKEY_CURRENT_USER = object()
        REG_SZ = 1

        def CreateKey(self, *a, **k):
            raise OSError("access denied")

    monkeypatch.setattr(channel, "winreg", _Boom())
    assert channel.pin_production_channel() is False
