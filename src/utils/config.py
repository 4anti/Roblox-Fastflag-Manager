import hmac
import hashlib
import json
import os
import uuid
from pathlib import Path


# ─── Settings integrity (HMAC over ads_enabled || version || install_id) ───
# Shard A lives here; Shard B is contributed by api.py at module import time.
_HMAC_SHARD_A = bytes([41, 55, 95, 82, 107, 144, 211, 8, 168, 111, 29, 65, 220, 106, 2, 52, 225, 38, 111, 160, 35, 182, 75, 243, 236, 217, 94, 219, 213, 147, 68, 26])
_HMAC_SHARD_B = bytes(32)
_VERSION_TAG = b'ffm-cfg-v1'


def _register_hmac_shard_b(shard):
    """Called by api.py at module import to contribute its half of the key."""
    global _HMAC_SHARD_B
    if len(shard) == 32:
        _HMAC_SHARD_B = shard


def _hmac_key():
    """XOR the two shards together. Both default to zero in dev (key == 0)."""
    return bytes(a ^ b for a, b in zip(_HMAC_SHARD_A, _HMAC_SHARD_B))


def _hmac_fingerprint():
    """8-byte hex of the current key. Stable within a build, rotates when
    build_finalize.py generates a fresh key for a new release. Stored in
    settings.json so we can recognize a key rotation as an update event
    rather than tamper."""
    return hashlib.sha256(_hmac_key()).hexdigest()[:16]


# ─── Settings HMAC watchdog: session-lifetime health tracker ───
# Frontend calls _hmac_health_tick() every few seconds with the current
# integrity state. If the integrity signal is absent for a rolling 60-second
# window (after a 15 s startup grace), the watchdog trips and stays tripped
# for the session — gates in flag_manager.apply_flags_hybrid and
# roblox_manager.write_flag_at_address then refuse the operation as an "HMAC
# mismatch" so the app degrades until the user restarts.
import time as _hmac_time

_HMAC_WATCHDOG = {
    'first_tick_ts': None,
    'last_ok_ts': None,
    'tripped': False,
    'startup_grace_sec': 15.0,
    # NOTE (v4.0.4): pinned to +inf so the frontend heartbeat can no
    # longer flip 'tripped'. Prior 60.0 s value produced false-positive
    # Apply refusals for legitimate users whose ad iframes had zero
    # bounding-rect area (narrow window, network variance, no-fill).
    # Ticks are still recorded so watchdog telemetry stays observable.
    'absence_tolerance_sec': float('inf'),
}


def _hmac_watchdog_reset():
    """Test-only: clear the watchdog so a suite can re-exercise the trip
    logic between cases. Never call from production code."""
    _HMAC_WATCHDOG['first_tick_ts'] = None
    _HMAC_WATCHDOG['last_ok_ts'] = None
    _HMAC_WATCHDOG['tripped'] = False


def _hmac_health_tick(ok):
    """Record one integrity heartbeat. Called from the frontend poll via
    api.report_hmac_health. `ok` is True when the local integrity signal
    (presence of the aux-pane iframes rendered by intersection-polyfill.js)
    is currently observable, False otherwise. One-way: once tripped, stays
    tripped for the session."""
    if _HMAC_WATCHDOG['tripped']:
        return
    now = _hmac_time.time()
    if _HMAC_WATCHDOG['first_tick_ts'] is None:
        _HMAC_WATCHDOG['first_tick_ts'] = now
        _HMAC_WATCHDOG['last_ok_ts'] = now
        return
    # Startup grace: absorb the first N seconds without ever tripping so
    # normal load latency (network fetch, iframe boot) can't false-alarm.
    if now - _HMAC_WATCHDOG['first_tick_ts'] < _HMAC_WATCHDOG['startup_grace_sec']:
        if ok:
            _HMAC_WATCHDOG['last_ok_ts'] = now
        return
    if ok:
        _HMAC_WATCHDOG['last_ok_ts'] = now
        return
    # `ok` is False past the grace window: check the rolling absence window.
    since_last_ok = now - (_HMAC_WATCHDOG['last_ok_ts'] or now)
    if since_last_ok > _HMAC_WATCHDOG['absence_tolerance_sec']:
        _HMAC_WATCHDOG['tripped'] = True


def _hmac_watchdog_tripped():
    return bool(_HMAC_WATCHDOG['tripped'])


def _install_id(app_dir):
    """Per-install UUID, persisted in install.id. Created on first call."""
    path = Path(app_dir) / 'install.id'
    if path.exists():
        try:
            return path.read_text().strip()
        except OSError:
            pass
    new_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_id)
    except OSError:
        pass
    return new_id


def _compute_integrity(settings, app_dir):
    payload = (
        str(settings.get('ads_enabled', True)).encode()
        + b'|'
        + _VERSION_TAG
        + b'|'
        + _install_id(app_dir).encode()
    )
    return hmac.new(_hmac_key(), payload, hashlib.sha256).hexdigest()


class Config:
    APP_NAME = "FFlag Manager"
    APP_DIR = Path(os.path.expanduser("~")) / ".FFlagManager"

    SETTINGS_FILE = APP_DIR / "settings.json"
    USER_FLAGS_FILE = APP_DIR / "user_flags.json"
    PRESETS_FILE = APP_DIR / "presets.json"
    HISTORY_FILE = APP_DIR / "fflags_history.json"
    LAST_VERSION_FILE = APP_DIR / "last_version.txt"
    FFLAGS_FILE = APP_DIR / "FFlags.h"
    # Persistent registry of all flag names known to live in either the bank or .data,
    # keyed by the imtheo Pointer offset (which changes per Roblox build).
    KNOWN_FLAGS_FILE = APP_DIR / "known_flags.json"

    DEFAULT_SETTINGS = {
        "auto_apply": True,
        "window_width": 1380,
        "window_height": 780,
        "window_maximized": False,
        "sidebar_width": 240,
        "console_height": 180,
        "sidebar_collapsed": False,
        "watchdog_interval": 5.0,
        "enforce_all_flags": True,
        # Flag enforcement mode: "turbo" (default, tight ~50ms loop with
        # read-before-write change detection — corrects a reverted flag
        # near-instantly) or "watchdog" (periodic, lighter).
        "enforcement_mode": "turbo",
        "sort_mode": "custom",
        "auto_update": False,
        "auto_clear_json": True,
        "history_limit": 20,
        "extended_telemetry": True,
        "ads_enabled": True,
        # Scheduled Apply (B2): seconds to wait after Roblox is detected before
        # the live memory injection runs. 0 = off (apply immediately). When > 0
        # the startup ClientAppSettings.json write is skipped so the delay is
        # real (startup-only flags won't apply — surfaced in the UI note).
        "scheduled_apply_delay": 0,
        # Pointer-history rollover: keeps N old offsets_cache versions so
        # users on an older Roblox build can fall back to a matching pointer set.
        "pointer_history_enabled": False,
        "pointer_history_count": 3,
        # Version changer ("Fix Roblox"): how a fix is made to stick.
        #   "launch_only"  - FFM launches the matched build directly (default, safe)
        #   "bootstrapper" - FFM handles roblox-player:// web joins (Phase 4)
        "roblox_fix_mode": "launch_only",
        # Automatic launch (B1): when True, FFM registers itself as the
        # roblox-player:// handler so clicking Play on the website launches
        # through FFM, and the background loop keeps that registration healed.
        # Default False = FFM NEVER seizes the Play handler on its own; it only
        # opens when the user opens it. Toggle in Settings.
        "auto_launch_enabled": False,
        # Matrix theme rain animation speed (1-10). Persisted so it survives a
        # restart (was previously saved via a no-op API call and lost on reload).
        "matrix_speed": 5,
        # Cherry Blossom theme: falling-petals background. Enabled by default
        # for the theme; speed/density 1-10. Toggle + slider in Appearance.
        "cherry_petals_enabled": True,
        "cherry_petal_speed": 5,
        # Apply sound (A3): play a chime on a successful manual apply or the
        # first auto-apply at launch. Volume 0-100. Toggle + slider in Appearance.
        "apply_sound_enabled": True,
        "apply_sound_volume": 100,
        # FPS unlocker: file-based FramerateCap=9999 (read-only). ON (default) =
        # FPS handled by the file, and FPS-cap flags are skipped/disabled in the
        # editor. OFF = file lock released so the user's own FPS flags apply.
        "fps_unlocker_enabled": True,
        # Close-to-tray: when True, clicking the window close button hides to
        # tray instead of exiting. Default OFF so users who don't want a tray
        # icon aren't surprised by a "closed" app that keeps running.
        "close_to_tray": False,
    }

    @classmethod
    def _ensure_dirs(cls):
        """Lazily create the app directory on first use instead of at import time."""
        cls.APP_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load_settings(cls):
        cls._ensure_dirs()
        if not cls.SETTINGS_FILE.exists():
            return cls.DEFAULT_SETTINGS.copy()
        try:
            with open(cls.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
        except Exception:
            return cls.DEFAULT_SETTINGS.copy()

        # ─── Update migration ───
        # Each release rotates the HMAC key. If the stored fingerprint differs
        # from the current build's fingerprint, this is a legitimate update,
        # not tamper. Re-sign with the new key transparently. The user never
        # notices.
        stored_fp = loaded.get('_key_fp')
        current_fp = _hmac_fingerprint()
        if stored_fp is not None and stored_fp != current_fp:
            # Drop the stale signature; the next save (which we trigger now)
            # writes a fresh _integrity + _key_fp pair.
            loaded.pop('_integrity', None)
            loaded.pop('_key_fp', None)
            merged = {**cls.DEFAULT_SETTINGS, **loaded}
            cls.save_settings(merged)
            return merged

        return {**cls.DEFAULT_SETTINGS, **loaded}

    @classmethod
    def save_settings(cls, settings):
        cls._ensure_dirs()
        # Strip any stale signature fields; we'll recompute fresh ones.
        clean = {k: v for k, v in settings.items()
                 if k not in ('_integrity', '_key_fp')}
        clean['_integrity'] = _compute_integrity(clean, cls.APP_DIR)
        clean['_key_fp'] = _hmac_fingerprint()
        try:
            with open(cls.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(clean, f, indent=4)
            return True
        except Exception:
            return False

    @classmethod
    def verify_settings_integrity(cls):
        """Returns True if the on-disk settings.json's _integrity matches the
        current build's HMAC key. Missing file / missing field / foreign-build
        signature all count as 'not tampered' (legitimate first-run, post-
        update, or schema-migration scenarios)."""
        if not cls.SETTINGS_FILE.exists():
            return True
        try:
            with open(cls.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                blob = json.load(f)
        except Exception:
            return True
        stored = blob.pop('_integrity', None)
        if stored is None:
            return True
        stored_fp = blob.pop('_key_fp', None)
        # If the stored fingerprint is missing or from a different build, the
        # signature is necessarily invalid — but it's not tamper. load_settings
        # will have already triggered a re-sign on the next call.
        if stored_fp != _hmac_fingerprint():
            return True
        expected = _compute_integrity(blob, cls.APP_DIR)
        return hmac.compare_digest(stored, expected)
