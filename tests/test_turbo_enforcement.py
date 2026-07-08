"""Smart-Turbo change-detection: only re-write a live flag when it has drifted."""
from src.core.flag_manager import should_write_flag


def test_unchanged_values_are_not_rewritten():
    assert should_write_flag("240", "240", "int") is False
    assert should_write_flag("true", "true", "bool") is False
    assert should_write_flag("1.5", "1.5", "float") is False


def test_drift_triggers_a_write():
    assert should_write_flag("60", "240", "int") is True
    assert should_write_flag("false", "true", "bool") is True
    assert should_write_flag("1.0", "2.0", "float") is True


def test_unreadable_value_always_writes():
    # read_flag_at_address returns None on a failed read or a string flag.
    assert should_write_flag(None, "240", "int") is True


def test_int_equivalent_forms_match():
    # "240" vs "240.0" should be treated as equal (no churn).
    assert should_write_flag("240", "240.0", "int") is False


def test_bool_synonyms_match():
    assert should_write_flag("true", "1", "bool") is False
    assert should_write_flag("false", "0", "bool") is False


def test_float_rounding_within_tolerance_is_equal():
    assert should_write_flag("1.50001", "1.5", "float") is False
    assert should_write_flag("1.5", "1.7", "float") is True
