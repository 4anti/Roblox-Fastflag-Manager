"""Bootstrapper mode: register FFM as the Roblox web-join handler so website
joins route through FFM (update-if-needed -> launch pinned build -> inject).

Roblox web joins arrive on TWO schemes, and Froststrap/Bloxstrap register both so
every launch variant routes through the bootstrapper (verified against
Froststrap/Froststrap `Utility/WindowsRegistry.cs::RegisterPlayer`):
  roblox-player:  - what the site's Play button uses (primary; drives handler
                    classification)
  roblox:         - deep links / legacy join flows
Each registers HKCU\\Software\\Classes\\<scheme> with a `URL Protocol` marker (the
value Windows needs to treat it as launchable), a DefaultIcon, and the
shell\\open\\command. The low-level _read/_write/_delete helpers wrap winreg so the
orchestration is unit-testable with them monkeypatched.
"""

from __future__ import annotations

import sys
from typing import Optional

from src.utils.logger import log

_SUBKEY_TMPL = r"Software\Classes\{}"
_HANDLER_ARG = "--roblox-handler"
_KNOWN_BOOTSTRAPPERS = ("bloxstrap", "fishstrap", "voidstrap")
# Both Roblox web-join schemes. Primary (index 0) is what the website Play button
# uses and is the one classify/seize decisions read.
_SCHEMES = ("roblox-player", "roblox")
_PRIMARY_SCHEME = _SCHEMES[0]


# ---- launch URI ----

def parse_launch_uri(uri: str) -> dict:
    """Parse a roblox: / roblox-player: join URI into a dict. Each '+'-delimited
    token is split on its FIRST ':' (values like placelauncherurl contain ':')."""
    if not uri:
        return {}
    for prefix in ("roblox-player:", "roblox:"):
        if uri.startswith(prefix):
            body = uri[len(prefix):]
            break
    else:
        return {}
    out: dict = {}
    for token in body.split("+"):
        if ":" in token:
            k, v = token.split(":", 1)
            out[k] = v
    return out


# ---- handler classification + seize policy ----

def classify_handler(command: Optional[str]) -> str:
    """Classify the current shell\\open\\command value:
    'ffm' | 'third_party' | 'stock' | 'none'."""
    if not command:
        return "none"
    low = command.lower()
    if _HANDLER_ARG in low or "fflagmanager.exe" in low or "fflag manager" in low:
        return "ffm"
    if any(name in low for name in _KNOWN_BOOTSTRAPPERS):
        return "third_party"
    if "robloxplayerbeta.exe" in low or "robloxplayerlauncher.exe" in low:
        return "stock"
    return "third_party"  # unknown owner -> treat as third party (don't clobber silently)


def should_seize(handler_class: str, fixable: bool) -> bool:
    """Whether to register FFM as the handler now.
    - already ours -> no.
    - third party -> only if there's a fixable version mismatch.
    - stock / none -> yes (user opted in)."""
    if handler_class == "ffm":
        return False
    if handler_class == "third_party":
        return bool(fixable)
    return True  # stock or none


# ---- registry layer (winreg wrappers; monkeypatched in tests) ----

def _subkey(scheme: str) -> str:
    return _SUBKEY_TMPL.format(scheme)


def _read_command(scheme: str = _PRIMARY_SCHEME) -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            _subkey(scheme) + r"\shell\open\command") as k:
            val, _ = winreg.QueryValueEx(k, "")
            return val
    except OSError:
        return None


def _has_url_protocol(scheme: str = _PRIMARY_SCHEME) -> bool:
    """True if the scheme's base key carries the 'URL Protocol' marker Windows
    requires to recognize it as a launchable URL scheme."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _subkey(scheme)) as k:
            winreg.QueryValueEx(k, "URL Protocol")
            return True
    except OSError:
        return False


def _write_scheme_values(scheme: str) -> None:
    """Write ONLY the base-key marker values that make a scheme a valid URL scheme
    ((Default) + empty 'URL Protocol'). Never touches shell\\open\\command, so it
    repairs the scheme without changing which handler owns it."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _subkey(scheme)) as base:
        winreg.SetValueEx(base, "", 0, winreg.REG_SZ, "URL: Roblox Protocol")
        winreg.SetValueEx(base, "URL Protocol", 0, winreg.REG_SZ, "")


def _write_command(scheme: str, command: str, handler: Optional[str] = None) -> None:
    """Register one scheme: base marker values, an optional DefaultIcon (the handler
    exe), and the shell\\open\\command. Mirrors Froststrap's RegisterProtocol."""
    import winreg
    _write_scheme_values(scheme)
    if handler:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              _subkey(scheme) + r"\DefaultIcon") as icon:
            winreg.SetValueEx(icon, "", 0, winreg.REG_SZ, handler)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          _subkey(scheme) + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)


def _delete_key(scheme: str) -> None:
    import winreg
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell",
                r"\DefaultIcon", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _subkey(scheme) + sub)
        except OSError:
            pass


# ---- orchestration ----

def current_handler_class() -> str:
    return classify_handler(_read_command(_PRIMARY_SCHEME))


def repair_scheme() -> bool:
    """Heal corrupt roblox / roblox-player schemes so the CURRENT handler (stock or
    FFM) is launchable from the website.

    A key that has shell\\open\\command but is missing the 'URL Protocol' marker is
    NOT recognized as a URL scheme by browsers, so clicking Play silently does
    nothing. This rewrites only the base marker values, leaving the command (i.e.
    who owns the handler) untouched — so it does NOT seize the handler and is safe
    to run even when Automatic Launch is off. Idempotent. Returns True if it
    repaired anything."""
    repaired = False
    for scheme in _SCHEMES:
        if _read_command(scheme) is None:
            continue  # no handler on this scheme -> nothing to repair
        if _has_url_protocol(scheme):
            continue  # already valid
        try:
            _write_scheme_values(scheme)
            repaired = True
        except OSError as e:
            log(f"[!] {scheme} scheme repair failed: {e}", (255, 120, 120))
    if repaired:
        log("[+] Repaired Roblox URL scheme(s) (were missing 'URL Protocol')",
            (100, 255, 100))
    return repaired


def register(handler_exe: str, script: Optional[str] = None) -> dict:
    """Back up the existing commands, then register FFM as the handler for every
    Roblox web-join scheme (roblox-player: and roblox:), matching Froststrap.

    `handler_exe` is the launcher (FFM.exe when frozen, or the Python interpreter
    when running from source). `script` MUST be supplied for a source run (the
    main.pyw path) — otherwise the handler would run the interpreter with no script
    and silently fail. Returns a {scheme: backed-up-command-or-None} map for a
    faithful restore."""
    if script:
        command = f'"{handler_exe}" "{script}" {_HANDLER_ARG} "%1"'
    else:
        command = f'"{handler_exe}" {_HANDLER_ARG} "%1"'
    backups: dict = {}
    for scheme in _SCHEMES:
        prev = _read_command(scheme)
        if prev and _HANDLER_ARG in prev:
            prev = None  # don't back up our own stale value
        backups[scheme] = prev
        _write_command(scheme, command, handler=handler_exe)
    log("[+] FFM registered as roblox / roblox-player handler", (100, 255, 100))
    return backups


def restore(backup) -> None:
    """Restore previously backed-up handler commands, or delete FFM's keys when
    there was nothing to restore.

    Accepts the {scheme: command} map from register(), or — for backward
    compatibility with a previously persisted value — a single legacy command
    string / None (applied to the primary scheme)."""
    if backup is None or isinstance(backup, str):
        backup = {_PRIMARY_SCHEME: backup}  # legacy single-value form
    restored = False
    for scheme in _SCHEMES:
        prev = backup.get(scheme) if isinstance(backup, dict) else None
        if prev:
            _write_command(scheme, prev)  # no DefaultIcon: let the prior owner set it
            restored = True
        else:
            _delete_key(scheme)
    if restored:
        log("[+] Restored previous Roblox handler(s)", (100, 255, 100))
    else:
        log("[+] Removed FFM Roblox handler(s)", (100, 255, 100))
