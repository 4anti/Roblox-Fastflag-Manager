"""B1-fast: a tiny on-disk cache so a website "Play" join can skip its
pre-launch network checks when the installed Roblox build is already known to be
the latest production build.

The protocol handler that launches Roblox on each join runs in a separate,
short-lived process, so it can't see the running FFM instance's in-memory state.
The running instance keeps this small file warm (every ~5 min, via the auto
version loop); the handler reads it and, when it says "already up to date and
fresh", launches Roblox immediately with no network round-trips.

Safety: this is a pure fast-path. Any miss, staleness, or error makes callers
fall back to the full (network) check — it can never block or break a join.
"""
import json
import os
import time

from src.utils.paths import user_data_dir

PATH = os.path.join(user_data_dir(), "join_fastpath.json")

# How long a "build is latest" confirmation is trusted before we re-check over
# the network. Kept short so we never join on a badly stale assumption; the
# background loop refreshes well within this window.
TTL_SECONDS = 600  # 10 minutes


def write_known_good(installed: str, latest: str) -> None:
    """Record that, as of now, the installed build is `installed` and the latest
    production build is `latest`. Best-effort — never raises."""
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"installed": installed, "latest": latest, "ts": int(time.time())},
                f,
            )
    except Exception:
        pass


def is_up_to_date(installed: str) -> bool:
    """True if a fresh cache entry confirms `installed` is already the latest
    production build, so the join can skip its update check and launch now.
    Returns False on any missing/stale/unreadable cache or mismatch."""
    if not installed or installed == "unknown":
        return False
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    try:
        if int(time.time()) - int(data.get("ts", 0)) > TTL_SECONDS:
            return False
    except Exception:
        return False
    # The build we're about to launch must equal the cached "latest".
    return data.get("latest") == installed
