"""File-based FPS unlock (GlobalBasicSettings FramerateCap) + FPS-flag skip."""
import os
import stat

from src.core import fps_unlocker
from src.utils.helpers import is_fps_flag

SAMPLE = (
    '<roblox version="4">\n'
    '\t<Item class="UserGameSettings">\n'
    '\t\t<Properties>\n'
    '\t\t\t<int name="FramerateCap">400</int>\n'
    '\t\t\t<bool name="Fullscreen">false</bool>\n'
    '\t\t</Properties>\n'
    '\t</Item>\n'
    '</roblox>\n'
)


def test_set_framerate_cap_replaces_existing():
    out = fps_unlocker.set_framerate_cap(SAMPLE, 9999)
    assert '<int name="FramerateCap">9999</int>' in out
    assert '400' not in out  # old value gone
    assert '<bool name="Fullscreen">false</bool>' in out  # rest untouched


def test_set_framerate_cap_inserts_when_absent():
    no_cap = SAMPLE.replace('\t\t\t<int name="FramerateCap">400</int>\n', '')
    out = fps_unlocker.set_framerate_cap(no_cap, 9999)
    assert '<int name="FramerateCap">9999</int>' in out


def test_is_unlocked():
    assert fps_unlocker.is_unlocked(fps_unlocker.set_framerate_cap(SAMPLE)) is True
    assert fps_unlocker.is_unlocked(SAMPLE) is False


def test_unlock_fps_sets_value_and_readonly(tmp_path, monkeypatch):
    p = tmp_path / "GlobalBasicSettings_13.xml"
    p.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(fps_unlocker, "settings_path", lambda: str(p))

    changed, _ = fps_unlocker.unlock_fps()
    assert changed is True
    assert '<int name="FramerateCap">9999</int>' in p.read_text(encoding="utf-8")
    assert not os.access(str(p), os.W_OK)  # now read-only

    # Idempotent: second run is a no-op.
    changed2, msg2 = fps_unlocker.unlock_fps()
    assert changed2 is False and "already" in msg2.lower()

    os.chmod(str(p), stat.S_IWRITE)  # let tmp cleanup remove it


def test_is_fps_flag_known():
    assert is_fps_flag("DFIntTaskSchedulerTargetFps") is True
    assert is_fps_flag("FIntTaskSchedulerTargetFps") is True
    assert is_fps_flag("FFlagTaskSchedulerLimitTargetFpsTo2402") is True


def test_is_fps_flag_non_fps():
    assert is_fps_flag("DFIntDebugFRMQualityLevelOverride") is False
    assert is_fps_flag("FFlagDebugSkyGray") is False
