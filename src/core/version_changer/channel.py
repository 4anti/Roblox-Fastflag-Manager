"""Keep the Roblox player on the production channel around each launch.

RobloxPlayerBeta.exe reads
HKCU\\SOFTWARE\\ROBLOX Corporation\\Environments\\RobloxPlayer\\Channel
value ``www.roblox.com``. An empty string means production (Froststrap
writes the same). Join URIs that carry ``channel:<name>`` are rewritten to
``channel:production`` so a leftover non-production token cannot send the
client to another deploy.
"""
from __future__ import annotations

import re
from typing import Optional

from src.utils.logger import log

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only
    winreg = None

_PLAYER_CHANNEL_KEY = r"SOFTWARE\ROBLOX Corporation\Environments\RobloxPlayer\Channel"
_PLAYER_CHANNEL_VALUE = "www.roblox.com"
_RX_CHANNEL_TOKEN = re.compile(r"channel:[a-zA-Z0-9\-_]+", re.IGNORECASE)


def rewrite_launch_args_channel(args: Optional[str]) -> Optional[str]:
    """Replace any ``channel:<token>`` in a join URI / cmdline with production."""
    if not args:
        return args
    return _RX_CHANNEL_TOKEN.sub("channel:production", args)


def pin_production_channel() -> bool:
    """Write the empty production channel value. Best-effort; never raises."""
    try:
        _write_player_channel("")
        return True
    except Exception as exc:
        log(f"[!] Production channel pin skipped: {exc}", (255, 200, 100))
        return False


def _write_player_channel(value: str) -> None:
    if winreg is None:
        raise RuntimeError("winreg unavailable")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PLAYER_CHANNEL_KEY) as key:
        winreg.SetValueEx(key, _PLAYER_CHANNEL_VALUE, 0, winreg.REG_SZ, value)
