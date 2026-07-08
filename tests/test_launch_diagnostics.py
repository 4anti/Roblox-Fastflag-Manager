from src.core.roblox_manager import RobloxManager


def test_launch_error_reason_known_codes():
    assert "administrator" in RobloxManager._launch_error_reason(5).lower()
    assert "uac" in RobloxManager._launch_error_reason(740).lower()
    assert "missing" in RobloxManager._launch_error_reason(2).lower()
    assert "cancelled" in RobloxManager._launch_error_reason(1223).lower()


def test_launch_error_reason_unknown_code_has_fallback():
    msg = RobloxManager._launch_error_reason(999999)
    assert isinstance(msg, str) and msg  # non-empty fallback


def test_is_roblox_running_returns_bool_without_crashing():
    # We can't guarantee Roblox state in CI, but the probe must be safe.
    assert RobloxManager.is_roblox_running() in (True, False)
