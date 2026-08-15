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


def test_run_upgrade_creates_missing_versions_root(tmp_path, monkeypatch):
    versions_root = tmp_path / "Roblox" / "Versions"
    assert not versions_root.exists()
    _stub_one_package_download(monkeypatch)

    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[])
    assert result["ok"] is True
    assert result["state"] == "installed"
    assert os.path.isdir(os.path.join(str(versions_root), "version-new"))


def test_complete_player_build_requires_nonempty_exe(tmp_path):
    missing = tmp_path / "missing"
    assert fixer._is_complete_player_build(str(missing)) is False

    empty = tmp_path / "empty"
    empty.mkdir()
    assert fixer._is_complete_player_build(str(empty)) is False

    zero = tmp_path / "zero"
    zero.mkdir()
    (zero / "RobloxPlayerBeta.exe").write_bytes(b"")
    assert fixer._is_complete_player_build(str(zero)) is False

    complete = tmp_path / "complete"
    complete.mkdir()
    (complete / "RobloxPlayerBeta.exe").write_bytes(b"MZ")
    assert fixer._is_complete_player_build(str(complete)) is True


def _stub_one_package_download(monkeypatch):
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


def test_run_upgrade_complete_folder_is_already_present(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"
    dest = versions_root / "version-new"
    dest.mkdir(parents=True)
    (dest / "RobloxPlayerBeta.exe").write_bytes(b"MZ")

    def boom(*_a, **_k):
        raise AssertionError("must not fetch a complete install")
    monkeypatch.setattr(fixer, "_get_manifest_packages", boom)

    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[])
    assert result["ok"] is True
    assert result["state"] == "already_present"
    assert result["final_path"] == str(dest)


def test_run_upgrade_incomplete_folder_is_not_already_present(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"
    leftover = versions_root / "version-new"
    leftover.mkdir(parents=True)
    (leftover / "AppSettings.xml").write_text("leftover", encoding="utf-8")
    _stub_one_package_download(monkeypatch)

    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[])
    assert result["ok"] is True
    assert result["state"] == "installed"
    assert os.path.isfile(os.path.join(str(versions_root), "version-new", "AppSettings.xml"))
    # Leftover-only content must not survive the replace.
    leftover_xml = (versions_root / "version-new" / "AppSettings.xml").read_text(encoding="utf-8")
    assert leftover_xml != "leftover"


def test_run_upgrade_zero_byte_exe_is_not_already_present(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"
    leftover = versions_root / "version-new"
    leftover.mkdir(parents=True)
    (leftover / "RobloxPlayerBeta.exe").write_bytes(b"")
    _stub_one_package_download(monkeypatch)

    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[])
    assert result["ok"] is True
    assert result["state"] == "installed"


def test_run_upgrade_replaces_incomplete_on_commit_collision(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"
    versions_root.mkdir()
    _stub_one_package_download(monkeypatch)

    real_commit = installer.commit_build
    calls = {"n": 0}

    def flaky_commit(staging_root, versions_root_arg, guid):
        calls["n"] += 1
        final_name = guid if guid.startswith("version-") else f"version-{guid}"
        dest = os.path.join(versions_root_arg, final_name)
        if calls["n"] == 1:
            os.makedirs(dest, exist_ok=True)
            raise FileExistsError(dest)
        return real_commit(staging_root, versions_root_arg, guid)

    monkeypatch.setattr(installer, "commit_build", flaky_commit)

    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[])
    assert result["ok"] is True
    assert result["state"] == "installed"
    assert os.path.isdir(os.path.join(str(versions_root), "version-new"))


def test_run_upgrade_commit_collision_complete_is_already_present(tmp_path, monkeypatch):
    versions_root = tmp_path / "Versions"
    versions_root.mkdir()
    _stub_one_package_download(monkeypatch)

    real_commit = installer.commit_build
    calls = {"n": 0}

    def flaky_commit(staging_root, versions_root_arg, guid):
        calls["n"] += 1
        final_name = guid if guid.startswith("version-") else f"version-{guid}"
        dest = os.path.join(versions_root_arg, final_name)
        if calls["n"] == 1:
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, "RobloxPlayerBeta.exe"), "wb") as f:
                f.write(b"MZ")
            raise FileExistsError(dest)
        return real_commit(staging_root, versions_root_arg, guid)

    monkeypatch.setattr(installer, "commit_build", flaky_commit)

    result = fixer.run_upgrade("version-new", str(versions_root), cache_dirs=[])
    assert result["ok"] is True
    assert result["state"] == "already_present"
    assert calls["n"] == 1
    assert os.path.isfile(os.path.join(str(versions_root), "version-new", "RobloxPlayerBeta.exe"))
