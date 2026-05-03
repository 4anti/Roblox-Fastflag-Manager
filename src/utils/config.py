import json
import os
from pathlib import Path

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
        "auto_apply": False,
        "window_width": 1050,
        "window_height": 780,
        "window_maximized": False,
        "sidebar_width": 240,
        "console_height": 180,
        "sidebar_collapsed": False,
        "watchdog_interval": 5.0,
        "enforce_all_flags": True,
        "sort_mode": "custom",
        "auto_update": False
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
            # Merge defaults so new keys are always present
            return {**cls.DEFAULT_SETTINGS, **loaded}
        except Exception:
            return cls.DEFAULT_SETTINGS.copy()

    @classmethod
    def save_settings(cls, settings):
        cls._ensure_dirs()
        try:
            with open(cls.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            return True
        except Exception:
            return False
