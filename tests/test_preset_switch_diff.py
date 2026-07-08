"""Preset-switch diff: on switching presets we revert ONLY the flags the new
preset won't actively set (removed / now-disabled), not every old flag."""
from src.gui.api import _preset_switch_revert_set


def _f(name, enabled=True, value="1"):
    return {"name": name, "value": value, "type": "int", "enabled": enabled}


def test_shared_flags_are_not_reverted():
    old = [_f("DFIntA"), _f("DFIntB")]
    new = [_f("DFIntA"), _f("DFIntB")]
    assert _preset_switch_revert_set(old, new) == []  # full overlap -> nothing reverts


def test_only_removed_flags_revert():
    old = [_f("DFIntA"), _f("DFIntB"), _f("DFIntC")]
    new = [_f("DFIntA"), _f("DFIntC")]            # B removed
    out = _preset_switch_revert_set(old, new)
    assert [f["name"] for f in out] == ["DFIntB"]


def test_flag_disabled_in_new_is_reverted():
    old = [_f("DFIntA"), _f("DFIntB")]
    new = [_f("DFIntA"), _f("DFIntB", enabled=False)]  # B kept but turned off
    out = _preset_switch_revert_set(old, new)
    assert [f["name"] for f in out] == ["DFIntB"]


def test_disabled_old_flag_is_not_reverted():
    # A disabled old flag was never written -> nothing to revert.
    old = [_f("DFIntA", enabled=False), _f("DFIntB")]
    new = [_f("DFIntB")]
    out = _preset_switch_revert_set(old, new)
    assert [f["name"] for f in out] == []   # A was off; B is shared


def test_added_flags_dont_appear_in_revert_set():
    old = [_f("DFIntA")]
    new = [_f("DFIntA"), _f("DFIntNew")]
    assert _preset_switch_revert_set(old, new) == []


def test_prefix_insensitive_overlap():
    # Same clean name with different prefix forms still counts as shared.
    old = [{"name": "DFIntTargetFps", "value": "9", "type": "int", "enabled": True}]
    new = [{"name": "FIntTargetFps", "value": "9", "type": "int", "enabled": True}]
    # clean_flag_name strips the type prefix, so these match -> no revert.
    assert _preset_switch_revert_set(old, new) == []


def test_empty_and_malformed_inputs():
    assert _preset_switch_revert_set([], []) == []
    assert _preset_switch_revert_set(None, None) == []
    assert _preset_switch_revert_set([{"value": "1"}], []) == []  # no name -> skipped
