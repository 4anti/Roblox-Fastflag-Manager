"""Preset export: 'json-flags-only' must emit a flat Roblox-style FastFlag map
({name: "value"}, all values quoted), and 'json-full' must be gone."""
import json

import pytest

from src.gui.api import (
    _export_preset_format,
    _parse_preset_payload,
    PRESET_EXPORT_FORMATS,
)

PRESET = {
    "name": "Net",
    "id": "abc123",
    "color": "#ff6fb5",
    "added_at": 1700000000,
    "flags": [
        {"name": "DFIntTaskSchedulerTargetFps", "value": "9999", "type": "int",
         "bind": "F", "_internal": 1},
        {"name": "FFlagDebugSkyGray", "value": "True", "type": "bool"},
        {"name": "FStringRemoteAnimationSmoothingStrategy",
         "value": "ExponentialDecay", "type": "string"},
    ],
}


def test_json_full_format_removed():
    assert "json-full" not in PRESET_EXPORT_FORMATS
    with pytest.raises(ValueError):
        _export_preset_format(PRESET, "json-full")


def test_flags_only_is_flat_all_string_map():
    out = json.loads(_export_preset_format(PRESET, "json-flags-only"))
    assert out == {
        "DFIntTaskSchedulerTargetFps": "9999",
        "FFlagDebugSkyGray": "True",
        "FStringRemoteAnimationSmoothingStrategy": "ExponentialDecay",
    }
    # Every value quoted (Roblox ClientAppSettings.json format).
    assert all(isinstance(v, str) for v in out.values())
    # Full prefixed names kept as keys; no binds / internal fields leak in.
    assert "bind" not in _export_preset_format(PRESET, "json-flags-only")
    assert "_internal" not in _export_preset_format(PRESET, "json-flags-only")


def test_flags_only_preserves_order():
    out = _export_preset_format(PRESET, "json-flags-only")
    assert (out.index("DFIntTaskSchedulerTargetFps")
            < out.index("FFlagDebugSkyGray")
            < out.index("FStringRemoteAnimationSmoothingStrategy"))


def test_flags_only_round_trips_back_into_ffm():
    payload = _export_preset_format(PRESET, "json-flags-only")
    _name, flags = _parse_preset_payload(payload, source_name="Net.json")
    by = {f["name"]: f for f in flags}
    assert by["DFIntTaskSchedulerTargetFps"]["value"] == "9999"
    # Type is re-inferred from the prefix on import (nothing lost in the map).
    assert by["DFIntTaskSchedulerTargetFps"]["type"] == "int"
    assert by["FFlagDebugSkyGray"]["type"] == "bool"
    assert by["FStringRemoteAnimationSmoothingStrategy"]["type"] == "string"


def test_remaining_formats_still_produce_output():
    for fmt in ("base64", "json-with-binds", "txt"):
        assert _export_preset_format(PRESET, fmt)
