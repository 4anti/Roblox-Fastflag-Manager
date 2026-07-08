"""Tests for the generic, allowlisted save_setting() API + the new theme
preference keys in get_settings()."""
import pytest

from src.gui.api import Api
from src.utils.config import Config


@pytest.fixture
def api(monkeypatch):
    # Bypass the heavy __init__; we only exercise settings plumbing.
    a = Api.__new__(Api)
    a.settings = {}
    # Don't touch the real on-disk settings.json during tests.
    monkeypatch.setattr(Config, "save_settings", lambda *_a, **_k: True)
    return a


def test_save_setting_clamps_petal_speed(api):
    api.save_setting("cherry_petal_speed", 99)
    assert api.settings["cherry_petal_speed"] == 10
    api.save_setting("cherry_petal_speed", -3)
    assert api.settings["cherry_petal_speed"] == 1


def test_save_setting_coerces_petals_enabled_to_bool(api):
    api.save_setting("cherry_petals_enabled", 0)
    assert api.settings["cherry_petals_enabled"] is False
    api.save_setting("cherry_petals_enabled", 1)
    assert api.settings["cherry_petals_enabled"] is True


def test_save_setting_clamps_matrix_speed(api):
    api.save_setting("matrix_speed", 7)
    assert api.settings["matrix_speed"] == 7
    api.save_setting("matrix_speed", 0)
    assert api.settings["matrix_speed"] == 1


def test_save_setting_ignores_unknown_keys(api):
    api.save_setting("evil_key", "rm -rf")
    assert "evil_key" not in api.settings


def test_save_setting_ignores_bad_values(api):
    api.save_setting("cherry_petal_speed", "not-a-number")
    assert "cherry_petal_speed" not in api.settings


def test_get_settings_exposes_theme_prefs(api):
    api.flag_manager = None  # get_settings only reads self.settings here
    api.settings = {"cherry_petals_enabled": False, "cherry_petal_speed": 8,
                    "matrix_speed": 3}
    out = api.get_settings()
    assert out["cherry_petals_enabled"] is False
    assert out["cherry_petal_speed"] == 8
    assert out["matrix_speed"] == 3


def test_get_settings_defaults_when_absent(api):
    api.settings = {}
    out = api.get_settings()
    assert out["cherry_petals_enabled"] is True
    assert out["cherry_petal_speed"] == 5
    assert out["matrix_speed"] == 5
    # Apply sound (A3) defaults: on, full volume.
    assert out["apply_sound_enabled"] is True
    assert out["apply_sound_volume"] == 100


def test_save_setting_apply_sound(api):
    api.save_setting("apply_sound_enabled", 0)
    assert api.settings["apply_sound_enabled"] is False
    api.save_setting("apply_sound_volume", 150)   # clamps to 100
    assert api.settings["apply_sound_volume"] == 100
    api.save_setting("apply_sound_volume", -5)    # clamps to 0
    assert api.settings["apply_sound_volume"] == 0
