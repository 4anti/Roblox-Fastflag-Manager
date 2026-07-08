import os
import zipfile
import pytest
from src.core.version_changer import fixer, manifest, downloader, installer


def _make_zip(path, inner_name="f.bin"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(inner_name, b"x")


def test_run_upgrade_installs_from_downloads(tmp_path, monkeypatch):
    # Arrange: a 1-package manifest; nothing cached; download produces a real zip.
    versions_root = tmp_path / "Versions"
    versions_root.mkdir()
    pkgs = [{"name": "RobloxApp.zip", "md5": "x", "packed_size": 1, "size": 1}]
    monkeypatch.setattr(fixer, "_get_manifest_packages", lambda guid: pkgs)
    monkeypatch.setattr(downloader, "find_cached", lambda pkg, dirs: None)

    def fake_download(pkg, guid, staging, cb=None):
        p = os.path.join(staging, pkg["name"])
        _make_zip(p)
        if cb:
            cb(1, 1)
        return p
    monkeypatch.setattr(downloader, "download_package", fake_download)

    seen = []
    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[],
                               progress=lambda d, t, n: seen.append((d, t, n)))
    # Assert
    assert result["ok"] is True
    assert result["state"] == "installed"
    assert os.path.isdir(os.path.join(str(versions_root), "version-new"))
    assert os.path.isfile(os.path.join(str(versions_root), "version-new", "AppSettings.xml"))
    assert seen and seen[-1][0] == seen[-1][1]  # progress reached total


def test_run_upgrade_uses_cached_without_download(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"
    versions_root.mkdir()
    cached = tmp_path / "RobloxApp.zip"
    _make_zip(str(cached))
    pkgs = [{"name": "RobloxApp.zip", "md5": "x", "packed_size": 1, "size": 1}]
    monkeypatch.setattr(fixer, "_get_manifest_packages", lambda guid: pkgs)
    monkeypatch.setattr(downloader, "find_cached", lambda pkg, dirs: str(cached))

    def boom(*a, **k):
        raise AssertionError("should not download a cached package")
    monkeypatch.setattr(downloader, "download_package", boom)

    result = fixer.run_upgrade("version-c", str(versions_root), cache_dirs=[str(tmp_path)])
    assert result["ok"] is True
    assert os.path.isfile(os.path.join(str(versions_root), "version-c", "f.bin")
                          ) or os.path.isdir(os.path.join(str(versions_root), "version-c"))


def test_run_upgrade_aborts_on_manifest_failure(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"; versions_root.mkdir()
    monkeypatch.setattr(fixer, "_get_manifest_packages", lambda guid: None)
    result = fixer.run_upgrade("version-x", str(versions_root), cache_dirs=[])
    assert result["ok"] is False
    assert result["state"] == "manifest_failed"


def test_run_upgrade_cancels_before_commit(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"; versions_root.mkdir()
    pkgs = [{"name": "RobloxApp.zip", "md5": "x", "packed_size": 1, "size": 1}]
    monkeypatch.setattr(fixer, "_get_manifest_packages", lambda guid: pkgs)
    monkeypatch.setattr(downloader, "find_cached", lambda pkg, dirs: None)
    result = fixer.run_upgrade("version-x", str(versions_root), cache_dirs=[],
                               should_cancel=lambda: True)
    assert result["ok"] is False
    assert result["state"] == "cancelled"
    assert not os.path.exists(os.path.join(str(versions_root), "version-x"))


def test_run_upgrade_aborts_on_download_failure(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"; versions_root.mkdir()
    pkgs = [{"name": "RobloxApp.zip", "md5": "x", "packed_size": 1, "size": 1}]
    monkeypatch.setattr(fixer, "_get_manifest_packages", lambda guid: pkgs)
    monkeypatch.setattr(downloader, "find_cached", lambda pkg, dirs: None)
    monkeypatch.setattr(downloader, "download_package", lambda *a, **k: None)
    result = fixer.run_upgrade("version-x", str(versions_root), cache_dirs=[])
    assert result["ok"] is False
    assert result["state"] == "download_failed"
    assert not os.path.exists(os.path.join(str(versions_root), "version-x"))
