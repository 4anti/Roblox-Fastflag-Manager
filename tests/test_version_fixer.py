from src.core.version_changer import fixer


def test_no_roblox_when_installed_missing():
    # Arrange / Act / Assert
    assert fixer.decide_fix_action(None, "version-a") == "no_roblox"
    assert fixer.decide_fix_action("unknown", "version-a") == "no_roblox"


def test_refresh_failed_when_upstream_unknown():
    # Arrange: installed known, but the network probe returned nothing.
    assert fixer.decide_fix_action("version-a", None) == "refresh_failed"


def test_resolved_when_upstream_matches_installed():
    # Arrange: upstream dumper has caught up to the installed build.
    assert fixer.decide_fix_action("version-a", "version-a") == "resolved"


def test_needs_download_when_builds_differ():
    # Arrange: upstream targets a different build than installed -> Phase 2 work.
    assert fixer.decide_fix_action("version-a", "version-b") == "needs_download"
