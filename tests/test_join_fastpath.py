import json
import time

from src.core.version_changer import fastpath


def _point_cache(tmp_path, monkeypatch):
    p = tmp_path / "join_fastpath.json"
    monkeypatch.setattr(fastpath, "PATH", str(p))
    return p


def test_up_to_date_when_installed_equals_latest(tmp_path, monkeypatch):
    _point_cache(tmp_path, monkeypatch)
    fastpath.write_known_good("version-abc", "version-abc")
    assert fastpath.is_up_to_date("version-abc") is True


def test_not_up_to_date_when_behind_latest(tmp_path, monkeypatch):
    _point_cache(tmp_path, monkeypatch)
    fastpath.write_known_good("version-old", "version-new")
    # Installed build is behind latest => must NOT take the fast path.
    assert fastpath.is_up_to_date("version-old") is False


def test_not_up_to_date_when_cache_missing(tmp_path, monkeypatch):
    _point_cache(tmp_path, monkeypatch)
    assert fastpath.is_up_to_date("version-abc") is False


def test_not_up_to_date_when_stale(tmp_path, monkeypatch):
    p = _point_cache(tmp_path, monkeypatch)
    stale_ts = int(time.time()) - fastpath.TTL_SECONDS - 5
    p.write_text(
        json.dumps({"installed": "version-abc", "latest": "version-abc", "ts": stale_ts}),
        encoding="utf-8",
    )
    assert fastpath.is_up_to_date("version-abc") is False


def test_unknown_build_never_fast_paths(tmp_path, monkeypatch):
    _point_cache(tmp_path, monkeypatch)
    fastpath.write_known_good("unknown", "unknown")
    assert fastpath.is_up_to_date("unknown") is False
