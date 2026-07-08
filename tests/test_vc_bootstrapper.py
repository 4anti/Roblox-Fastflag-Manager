import pytest
from src.core.version_changer import bootstrapper as bs


# --- URI parsing ---

def test_parse_launch_uri_basic():
    uri = "roblox-player:1+launchmode:play+placelauncherurl:https%3A%2F%2Fx?a%3Db+channel:production"
    d = bs.parse_launch_uri(uri)
    assert d["launchmode"] == "play"
    assert d["channel"] == "production"
    # value with embedded colons (only first ':' splits)
    assert d["placelauncherurl"].startswith("https%3A")


def test_parse_launch_uri_non_protocol_returns_empty():
    assert bs.parse_launch_uri("not-a-uri") == {}


def test_parse_launch_uri_roblox_scheme():
    # roblox: (not just roblox-player:) must parse too — Froststrap registers both.
    d = bs.parse_launch_uri("roblox:1+launchmode:play+channel:production")
    assert d["launchmode"] == "play"
    assert d["channel"] == "production"


# --- handler classification ---

def test_classify_handler_ffm():
    assert bs.classify_handler(r'"C:\\Apps\\FFM\\FFlagManager.exe" --roblox-handler "%1"') == "ffm"


def test_classify_handler_third_party():
    assert bs.classify_handler(r'"C:\\x\\Bloxstrap.exe" -player "%1"') == "third_party"
    assert bs.classify_handler(r'"C:\\x\\Fishstrap.exe" -player "%1"') == "third_party"


def test_classify_handler_stock():
    assert bs.classify_handler(r'"C:\\Versions\\version-a\\RobloxPlayerBeta.exe" "%1"') == "stock"


def test_classify_handler_none():
    assert bs.classify_handler("") == "none"
    assert bs.classify_handler(None) == "none"


# --- seize decision ---

@pytest.mark.parametrize("handler,fixable,expected", [
    ("none", False, True),       # nothing there -> register
    ("stock", False, True),      # stock Roblox -> take over (opt-in)
    ("ffm", False, False),       # already ours -> no-op
    ("third_party", False, False),  # don't steal unless fixable
    ("third_party", True, True),    # fixable mismatch -> seize
])
def test_should_seize(handler, fixable, expected):
    assert bs.should_seize(handler, fixable) is expected


# --- register / restore against a fake registry ---
#
# The fake registry is keyed by scheme so both roblox-player: and roblox: are
# exercised. _write_command takes (scheme, command, handler=None); _read_command
# and _delete_key take (scheme).

def _fake_registry(monkeypatch, initial=None):
    """Install a scheme-keyed in-memory registry over the winreg helpers and return
    the backing dict {scheme: command}."""
    store = dict(initial or {})
    monkeypatch.setattr(bs, "_read_command", lambda scheme=bs._PRIMARY_SCHEME: store.get(scheme))
    monkeypatch.setattr(bs, "_write_command",
                        lambda scheme, command, handler=None: store.__setitem__(scheme, command))
    monkeypatch.setattr(bs, "_delete_key", lambda scheme: store.pop(scheme, None))
    return store


def test_register_covers_both_schemes(monkeypatch):
    # Froststrap parity: registering must claim BOTH roblox-player: and roblox:.
    store = _fake_registry(monkeypatch, {s: None for s in bs._SCHEMES})
    bs.register(r"C:\app\FFM.exe")
    for scheme in bs._SCHEMES:
        assert store[scheme] == r'"C:\app\FFM.exe" --roblox-handler "%1"'


def test_register_then_restore_roundtrip(monkeypatch):
    # Pre-existing third-party handler on the primary scheme; roblox: empty.
    initial = {"roblox-player": r'"C:\\old\\Bloxstrap.exe" -player "%1"', "roblox": None}
    store = _fake_registry(monkeypatch, initial)

    backup = bs.register(r"C:\Apps\FFM\FFlagManager.exe")
    # backup is now a per-scheme map
    assert backup["roblox-player"] == r'"C:\\old\\Bloxstrap.exe" -player "%1"'
    assert backup["roblox"] is None
    assert "FFlagManager.exe" in store["roblox-player"] and "--roblox-handler" in store["roblox-player"]
    assert "--roblox-handler" in store["roblox"]

    bs.restore(backup)
    # primary restored to the prior owner; the roblox: key we created is removed
    assert store["roblox-player"] == r'"C:\\old\\Bloxstrap.exe" -player "%1"'
    assert "roblox" not in store


def test_restore_deletes_when_no_backup(monkeypatch):
    store = _fake_registry(monkeypatch, {
        "roblox-player": r'"C:\Apps\FFM\FFlagManager.exe" --roblox-handler "%1"',
        "roblox": r'"C:\Apps\FFM\FFlagManager.exe" --roblox-handler "%1"',
    })
    bs.restore(None)
    assert store == {}


def test_restore_accepts_legacy_string_backup(monkeypatch):
    # Backward compat: a previously persisted single-string backup restores to the
    # primary scheme and clears roblox:.
    store = _fake_registry(monkeypatch, {
        "roblox-player": r'"C:\app\FFM.exe" --roblox-handler "%1"',
        "roblox": r'"C:\app\FFM.exe" --roblox-handler "%1"',
    })
    bs.restore(r'"C:\\old\\Bloxstrap.exe" -player "%1"')
    assert store["roblox-player"] == r'"C:\\old\\Bloxstrap.exe" -player "%1"'
    assert "roblox" not in store


def test_register_frozen_command_has_no_script(monkeypatch):
    # Frozen build: handler_exe is the .exe itself, no separate script.
    store = _fake_registry(monkeypatch, {s: None for s in bs._SCHEMES})
    bs.register(r"C:\app\FFM.exe")
    assert store["roblox-player"] == r'"C:\app\FFM.exe" --roblox-handler "%1"'


def test_register_source_command_includes_script(monkeypatch):
    # Source run: interpreter + script must BOTH be in the command, else the
    # registered handler runs pythonw.exe with no script and silently fails.
    store = _fake_registry(monkeypatch, {s: None for s in bs._SCHEMES})
    bs.register(r"C:\Py\pythonw.exe", script=r"C:\app\main.pyw")
    assert store["roblox-player"] == r'"C:\Py\pythonw.exe" "C:\app\main.pyw" --roblox-handler "%1"'
    # and it must still classify as ours
    assert bs.classify_handler(store["roblox-player"]) == "ffm"
