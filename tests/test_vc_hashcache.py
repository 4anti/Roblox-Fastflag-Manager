import hashlib

from src.core.version_changer import hashcache


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _point_cache(tmp_path, monkeypatch):
    p = tmp_path / "download_hashcache.json"
    monkeypatch.setattr(hashcache, "PATH", str(p))
    # Reset the lazy in-memory mirror so each test starts clean.
    monkeypatch.setattr(hashcache, "_cache", None)
    return p


def test_file_md5_matches_hashlib(tmp_path, monkeypatch):
    _point_cache(tmp_path, monkeypatch)
    f = tmp_path / "pkg.zip"
    f.write_bytes(b"hello")
    assert hashcache.file_md5(str(f)) == _md5(b"hello")


def test_file_md5_missing_file_returns_none(tmp_path, monkeypatch):
    _point_cache(tmp_path, monkeypatch)
    assert hashcache.file_md5(str(tmp_path / "nope.zip")) is None


def test_second_call_uses_cache_not_disk(tmp_path, monkeypatch):
    # Arrange: hash once so the entry is recorded.
    _point_cache(tmp_path, monkeypatch)
    f = tmp_path / "pkg.zip"
    f.write_bytes(b"hello")
    first = hashcache.file_md5(str(f))

    # If the second call re-reads the file it would crash here, proving it
    # served the answer from the cache (size + mtime unchanged).
    def _boom(*_a, **_k):
        raise AssertionError("file was re-read instead of using the cache")

    monkeypatch.setattr(hashcache, "_compute_md5", _boom)
    assert hashcache.file_md5(str(f)) == first


def test_changed_file_invalidates_cache(tmp_path, monkeypatch):
    # A file edited after caching (new size/mtime) must be re-hashed, not trusted.
    _point_cache(tmp_path, monkeypatch)
    f = tmp_path / "pkg.zip"
    f.write_bytes(b"hello")
    assert hashcache.file_md5(str(f)) == _md5(b"hello")

    f.write_bytes(b"changed payload")
    # Bump mtime explicitly so the change is visible even on coarse clocks.
    import os
    st = f.stat()
    os.utime(str(f), ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert hashcache.file_md5(str(f)) == _md5(b"changed payload")


def test_cache_persists_across_reload(tmp_path, monkeypatch):
    # A fresh process (reset in-memory mirror) should still read the entry off disk.
    p = _point_cache(tmp_path, monkeypatch)
    f = tmp_path / "pkg.zip"
    f.write_bytes(b"hello")
    hashcache.file_md5(str(f))
    assert p.exists()

    monkeypatch.setattr(hashcache, "_cache", None)  # simulate a new process

    def _boom(*_a, **_k):
        raise AssertionError("re-hashed despite a persisted cache entry")

    monkeypatch.setattr(hashcache, "_compute_md5", _boom)
    assert hashcache.file_md5(str(f)) == _md5(b"hello")


def test_save_prunes_missing_files(tmp_path, monkeypatch):
    p = _point_cache(tmp_path, monkeypatch)
    keep = tmp_path / "keep.zip"
    gone = tmp_path / "gone.zip"
    keep.write_bytes(b"a")
    gone.write_bytes(b"b")
    hashcache.file_md5(str(keep))
    hashcache.file_md5(str(gone))

    gone.unlink()
    # Trigger another save (new entry) so pruning runs.
    other = tmp_path / "other.zip"
    other.write_bytes(b"c")
    hashcache.file_md5(str(other))

    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    keys = {__import__("os").path.basename(k) for k in data}
    assert "gone.zip" not in keys
    assert "keep.zip" in keys


def test_find_cached_uses_hashcache(tmp_path, monkeypatch):
    # The downloader's cache lookup must route through the hash cache so repeat
    # fixes don't re-read every package.
    _point_cache(tmp_path, monkeypatch)
    from src.core.version_changer import downloader

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "RobloxApp.zip").write_bytes(b"data")
    pkg = {"name": "RobloxApp.zip", "md5": _md5(b"data")}

    # Prime the cache.
    assert downloader.find_cached(pkg, [str(cache)]) == str(cache / "RobloxApp.zip")

    # A second lookup must not re-hash the file.
    def _boom(*_a, **_k):
        raise AssertionError("find_cached re-hashed a cached package")

    monkeypatch.setattr(hashcache, "_compute_md5", _boom)
    assert downloader.find_cached(pkg, [str(cache)]) == str(cache / "RobloxApp.zip")
