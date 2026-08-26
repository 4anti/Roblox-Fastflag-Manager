"""Bogus DFlag prefix on pasted lists must map to dump names without
touching real DFFlag / DFInt / FFlag names."""
import json

from src.gui.api import _parse_preset_payload
from src.utils.config import Config
from src.utils.helpers import heal_dflag_flag_names, strip_bogus_dflag_prefix
from src.core.flag_manager import FlagManager


def test_strip_dflag_enable_per_frame():
    assert strip_bogus_dflag_prefix("DFlagEnablePerFrameSampling") == (
        "EnablePerFrameSampling"
    )


def test_strip_leaves_real_prefixes():
    assert strip_bogus_dflag_prefix("DFFlagEnablePerFrameSampling") == (
        "DFFlagEnablePerFrameSampling"
    )
    assert strip_bogus_dflag_prefix("DFIntAckPaceMaxBurstMs") == (
        "DFIntAckPaceMaxBurstMs"
    )
    assert strip_bogus_dflag_prefix("FFlagDebugSkyGray") == "FFlagDebugSkyGray"
    assert strip_bogus_dflag_prefix("EnablePerFrameSampling") == (
        "EnablePerFrameSampling"
    )


def test_heal_dedupes_dflag_and_bare():
    flags, changed = heal_dflag_flag_names([
        {"name": "DFlagEnablePerFrameSampling", "value": "true"},
        {"name": "EnablePerFrameSampling", "value": "false"},
        {"name": "DFFlagKeepMe", "value": "true"},
    ])
    assert changed is True
    names = [f["name"] for f in flags]
    assert names == ["EnablePerFrameSampling", "DFFlagKeepMe"]
    assert flags[0]["value"] == "true"


def test_parse_json_map_strips_dflag():
    raw = json.dumps({
        "DFlagEnablePerFrameSampling": True,
        "DFIntTaskSchedulerTargetFps": 240,
        "DFlagAckPaceMaxBurstMs": 0,
    })
    _, flags = _parse_preset_payload(raw, source_name="paste")
    by_name = {f["name"]: f["value"] for f in flags}
    assert "DFlagEnablePerFrameSampling" not in by_name
    assert "EnablePerFrameSampling" in by_name
    assert "DFIntTaskSchedulerTargetFps" in by_name
    assert "AckPaceMaxBurstMs" in by_name


def test_load_user_flags_heals_dflag(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(Config, "APP_DIR", app)
    monkeypatch.setattr(Config, "USER_FLAGS_FILE", app / "user_flags.json")
    monkeypatch.setattr(Config, "SETTINGS_FILE", app / "settings.json")
    (app / "user_flags.json").write_text(json.dumps([
        {
            "name": "DFlagEnablePerFrameSampling",
            "value": "True",
            "type": "bool",
        }
    ]), encoding="utf-8")

    fm = FlagManager.__new__(FlagManager)
    fm._lock = __import__("threading").Lock()
    fm.user_flags = []
    fm.load_user_flags()
    assert fm.user_flags[0]["name"] == "EnablePerFrameSampling"
    saved = json.loads((app / "user_flags.json").read_text(encoding="utf-8"))
    assert saved[0]["name"] == "EnablePerFrameSampling"
