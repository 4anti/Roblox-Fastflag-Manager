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


def test_card_aligned_when_all_three_match():
    assert fixer.classify_version_card(
        "version-A", "version-A", "version-A") == "aligned"


def test_card_offsets_lagging_when_user_already_on_latest():
    # User is on CDN latest; dump still names an older build. A Roblox
    # download cannot help — this is the flicker the Advanced card used
    # to paint as "fixed" then snap back to mismatch.
    assert fixer.classify_version_card(
        "version-LATEST", "version-OLD-DUMP", "version-LATEST") == "offsets_lagging"


def test_card_needs_update_when_install_behind_cdn():
    assert fixer.classify_version_card(
        "version-OLD", "version-LATEST", "version-LATEST") == "needs_roblox_update"


def test_card_aligned_update_available_when_dump_matches_old_install():
    assert fixer.classify_version_card(
        "version-OLD", "version-OLD", "version-LATEST") == "aligned_update_available"


def test_card_mismatch_offline_when_cdn_unknown():
    assert fixer.classify_version_card(
        "version-A", "version-B", None) == "mismatch_offline"


def test_card_no_roblox_and_pending():
    assert fixer.classify_version_card(None, "version-A", "version-A") == "no_roblox"
    assert fixer.classify_version_card("unknown", "version-A", "version-A") == "no_roblox"
    assert fixer.classify_version_card("version-A", None, "version-A") == "offsets_pending"
