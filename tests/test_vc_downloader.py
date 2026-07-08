import hashlib
import os
from src.core.version_changer import downloader


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def test_verify_md5_match(tmp_path):
    p = tmp_path / "pkg.zip"
    p.write_bytes(b"hello")
    assert downloader.verify_md5(str(p), _md5(b"hello")) is True


def test_verify_md5_mismatch(tmp_path):
    p = tmp_path / "pkg.zip"
    p.write_bytes(b"hello")
    assert downloader.verify_md5(str(p), _md5(b"different")) is False


def test_verify_md5_missing_file(tmp_path):
    assert downloader.verify_md5(str(tmp_path / "nope.zip"), "0" * 32) is False


def test_already_present_skips_when_cached(tmp_path):
    # Arrange: a cache dir already holds the package with the right checksum.
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "RobloxApp.zip").write_bytes(b"data")
    pkg = {"name": "RobloxApp.zip", "md5": _md5(b"data")}
    # Act / Assert
    assert downloader.find_cached(pkg, [str(cache)]) == str(cache / "RobloxApp.zip")


def test_find_cached_returns_none_on_checksum_mismatch(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "RobloxApp.zip").write_bytes(b"stale")
    pkg = {"name": "RobloxApp.zip", "md5": _md5(b"fresh")}
    assert downloader.find_cached(pkg, [str(cache)]) is None


def test_cdn_bases_use_real_cachefly_host():
    # Regression: the cachefly mirror is roblox-setup.cachefly.net, not a
    # *-cfly.rbxcdn.com host. Verified against bloxstraplabs/bloxstrap Deployment.cs.
    assert "https://roblox-setup.cachefly.net/" in downloader.CDN_BASES
    assert all("setup-cfly" not in b for b in downloader.CDN_BASES)
