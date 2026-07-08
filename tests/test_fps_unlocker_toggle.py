"""FPS Unlocker toggle: when ON, FPS-cap flags are skipped (file handles FPS);
when OFF, they apply normally and the file lock is released."""
import os
import stat

import pytest

from src.core import fps_unlocker
from src.core.flag_manager import _skip_fps
from src.gui.api import Api
from src.utils.config import Config


# ── gating helper ────────────────────────────────────────────────────────────

def test_skip_fps_gated_on_setting():
    fps_name = "DFIntTaskSchedulerTargetFps"
    other = "DFIntSomethingElse"
    # Unlocker ON -> FPS flag skipped; non-FPS untouched.
    assert _skip_fps(fps_name, True) is True
    assert _skip_fps(other, True) is False
    # Unlocker OFF -> FPS flag applies normally (not skipped).
    assert _skip_fps(fps_name, False) is False
    assert _skip_fps(other, False) is False


def test_skip_fps_matches_both_known_fps_flags():
    assert _skip_fps("FFlagTaskSchedulerLimitTargetFpsTo2402", True) is True
    assert _skip_fps("FIntTaskSchedulerTargetFps", True) is True


# ── restore_fps (the OFF undo) ───────────────────────────────────────────────

def test_restore_fps_clears_readonly(tmp_path, monkeypatch):
    p = tmp_path / "GlobalBasicSettings_13.xml"
    p.write_text('<Properties>\n<int name="FramerateCap">9999</int>\n</Properties>')
    os.chmod(str(p), stat.S_IREAD)  # lock it (what unlock_fps does)
    monkeypatch.setattr(fps_unlocker, "settings_path", lambda: str(p))
    assert not os.access(str(p), os.W_OK)

    changed, _msg = fps_unlocker.restore_fps()
    assert changed is True
    assert os.access(str(p), os.W_OK)        # lock released

    changed2, _msg2 = fps_unlocker.restore_fps()
    assert changed2 is False                 # idempotent


def test_restore_fps_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fps_unlocker, "settings_path",
                        lambda: str(tmp_path / "nope.xml"))
    changed, _ = fps_unlocker.restore_fps()
    assert changed is False


# ── api wiring ───────────────────────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    a = Api.__new__(Api)
    a.settings = {}
    a._window = None
    monkeypatch.setattr(Config, "save_settings", lambda *a, **k: True)
    return a


def test_get_settings_exposes_fps_default(api):
    api.flag_manager = None
    api.settings = {}
    assert api.get_settings()["fps_unlocker_enabled"] is True


def test_set_fps_unlocker_saves_bool_and_applies(api, monkeypatch):
    calls = {"unlock": 0, "restore": 0}
    monkeypatch.setattr(fps_unlocker, "unlock_fps",
                        lambda: (calls.__setitem__("unlock", calls["unlock"] + 1), (False, "stub"))[1])
    monkeypatch.setattr(fps_unlocker, "restore_fps",
                        lambda: (calls.__setitem__("restore", calls["restore"] + 1), (False, "stub"))[1])

    api.set_fps_unlocker(0)
    assert api.settings["fps_unlocker_enabled"] is False
    assert calls["restore"] == 1            # OFF -> released the lock

    api.set_fps_unlocker(1)
    assert api.settings["fps_unlocker_enabled"] is True
    assert calls["unlock"] == 1             # ON -> applied the unlock
