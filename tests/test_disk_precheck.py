import collections

from src.core.version_changer import fixer
from src.core.version_changer import downloader

Usage = collections.namedtuple("Usage", "total used free")

PACKAGES = [
    {"name": "a.zip", "md5": "x", "packed_size": 50_000_000, "size": 150_000_000},
    {"name": "b.zip", "md5": "y", "packed_size": 30_000_000, "size": 90_000_000},
]


def _no_cache(monkeypatch):
    monkeypatch.setattr(downloader, "find_cached", lambda pkg, dirs: None)


def test_precheck_passes_with_plenty_of_space(tmp_path, monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(fixer.shutil, "disk_usage", lambda p: Usage(0, 0, 50 * 1024**3))
    assert fixer.disk_space_precheck(PACKAGES, str(tmp_path), []) is None


def test_precheck_fails_when_temp_drive_full(tmp_path, monkeypatch):
    _no_cache(monkeypatch)
    # Only 10 MB free everywhere — far less than the ~320 MB needed.
    monkeypatch.setattr(fixer.shutil, "disk_usage", lambda p: Usage(0, 0, 10 * 1024**2))
    msg = fixer.disk_space_precheck(PACKAGES, str(tmp_path), [])
    assert msg is not None
    assert "space" in msg.lower()


def test_precheck_allows_on_measurement_error(tmp_path, monkeypatch):
    _no_cache(monkeypatch)

    def boom(_):
        raise OSError("cannot stat")

    monkeypatch.setattr(fixer.shutil, "disk_usage", boom)
    # A measurement failure must NOT block the upgrade.
    assert fixer.disk_space_precheck(PACKAGES, str(tmp_path), []) is None
