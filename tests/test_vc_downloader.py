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


def test_cdn_bases_excludes_dead_cachefly_host():
    # Regression (2026-07-09): `roblox-setup.cachefly.net` now returns
    # "Hostname not configured" — Roblox retired that mirror. Keeping it
    # in the fallback list wasted one full HTTP round-trip per package
    # download attempt. Verified live before removal (see
    # docs/AI_AGENT_HANDBOOK.md § "Roblox CDN inventory"). We also never
    # had the mis-spelled *-cfly.rbxcdn.com host — guard against it too.
    assert "roblox-setup.cachefly.net" not in " ".join(downloader.CDN_BASES)
    assert all("setup-cfly" not in b for b in downloader.CDN_BASES)


def test_cdn_bases_are_only_https_roblox_owned_hosts():
    # No HTTP fallback survives: manifest MD5 verification protects the
    # bytes but plain HTTP lets a hostile middlebox waste our retry budget
    # with garbage responses. Every base must be an https:// scheme, and
    # the host must live under rbxcdn.com or the raw S3 endpoint for
    # Roblox's own bucket.
    for base in downloader.CDN_BASES:
        assert base.startswith("https://"), f"non-https base: {base}"
        host = base.split("/", 3)[2]
        assert (host.endswith(".rbxcdn.com")
                or host == "s3.amazonaws.com"), f"unrecognised host: {host}"
