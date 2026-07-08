import pytest
from src.core.version_changer import fixer


def test_plan_upgrade_blocks_downgrade():
    # Arrange: target build is OLDER than installed -> must refuse (upgrade-only).
    decision = fixer.plan_upgrade(installed="version-bbbb", target="version-aaaa",
                                  is_older=lambda target, installed: True)
    # Assert
    assert decision["action"] == "blocked_downgrade"


def test_plan_upgrade_allows_when_target_newer():
    decision = fixer.plan_upgrade(installed="version-aaaa", target="version-bbbb",
                                  is_older=lambda target, installed: False)
    assert decision["action"] == "upgrade"
    assert decision["target"] == "version-bbbb"


def test_plan_upgrade_noop_when_already_matching():
    decision = fixer.plan_upgrade(installed="version-aaaa", target="version-aaaa",
                                  is_older=lambda target, installed: False)
    assert decision["action"] == "already_matching"


def test_plan_upgrade_handles_missing_target():
    decision = fixer.plan_upgrade(installed="version-aaaa", target=None,
                                  is_older=lambda target, installed: False)
    assert decision["action"] == "no_target"


def test_select_packages_to_fetch_dedupes_cached():
    # Arrange: two packages, one already cached.
    packages = [{"name": "a.zip", "md5": "1"}, {"name": "b.zip", "md5": "2"}]
    # 'a.zip' resolves to a cached path; 'b.zip' does not.
    fetch = fixer.select_packages_to_fetch(
        packages, find_cached=lambda pkg, dirs: "cached/a.zip" if pkg["name"] == "a.zip" else None,
        cache_dirs=["x"])
    assert [p["name"] for p in fetch] == ["b.zip"]
