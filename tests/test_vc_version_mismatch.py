from src.core.version_changer import fixer


def test_no_mismatch_when_builds_equal():
    assert fixer.is_version_mismatch("version-abc", "version-abc") is False


def test_mismatch_when_builds_differ():
    # The rename case: folder claims one build, offsets target another.
    assert fixer.is_version_mismatch("version-3grhgjwegwe", "version-abc") is True


def test_no_mismatch_when_no_roblox_installed():
    assert fixer.is_version_mismatch(None, "version-abc") is False
    assert fixer.is_version_mismatch("unknown", "version-abc") is False


def test_no_mismatch_when_offsets_target_unknown():
    # Offsets still loading / build couldn't be extracted -> never false-alarm.
    assert fixer.is_version_mismatch("version-abc", None) is False
    assert fixer.is_version_mismatch("version-abc", "") is False


def test_no_mismatch_when_both_unknown():
    assert fixer.is_version_mismatch(None, None) is False
