"""get_roblox_version_dir must not pick by directory mtime across launchers.

Writing ClientAppSettings.json bumps folder mtime, so an older leftover
(or a Bloxstrap tree) used to win over the real stock player exe.
"""
import os
import time

from src.core.roblox_manager import RobloxManager


def _mk_player(root, name, exe_mtime, dir_mtime):
    vdir = root / name
    vdir.mkdir(parents=True)
    exe = vdir / "RobloxPlayerBeta.exe"
    exe.write_bytes(b"x")
    os.utime(exe, (exe_mtime, exe_mtime))
    os.utime(vdir, (dir_mtime, dir_mtime))
    return vdir


def test_recently_written_older_stock_folder_cannot_beat_newer_exe(tmp_path, monkeypatch):
    """A leftover stock folder whose ClientAppSettings.json was just written
    (newer dir mtime, older exe) must lose to a newer player exe."""
    local = tmp_path / "Local"
    stock = local / "Roblox" / "Versions"
    now = time.time()
    old = _mk_player(stock, "version-OLD", exe_mtime=now - 200, dir_mtime=now)
    new = _mk_player(stock, "version-NEW", exe_mtime=now - 10, dir_mtime=now - 100)

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: None))

    picked = RobloxManager.get_roblox_version_dir()
    assert picked == str(new)
    assert picked != str(old)


def test_bloxstrap_newest_mtime_does_not_beat_stock_when_idle(tmp_path, monkeypatch):
    """No running PID: stock wins even if Bloxstrap's folder mtime is newest."""
    local = tmp_path / "Local"
    now = time.time()
    stock_dir = _mk_player(
        local / "Roblox" / "Versions", "version-STOCK",
        exe_mtime=now - 50, dir_mtime=now - 50)
    blox_dir = _mk_player(
        local / "Bloxstrap" / "Versions", "version-BLOX",
        exe_mtime=now, dir_mtime=now)

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: None))

    picked = RobloxManager.get_roblox_version_dir()
    assert picked == str(stock_dir)
    assert picked != str(blox_dir)


def test_running_image_path_wins_over_stock(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    now = time.time()
    stock_dir = _mk_player(
        local / "Roblox" / "Versions", "version-STOCK",
        exe_mtime=now, dir_mtime=now)
    blox_dir = _mk_player(
        local / "Bloxstrap" / "Versions", "version-BLOX",
        exe_mtime=now - 10, dir_mtime=now - 10)

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: str(blox_dir)))

    picked = RobloxManager.get_roblox_version_dir()
    assert picked == str(blox_dir)
    assert picked != str(stock_dir)


def test_bootstrapper_used_only_when_stock_has_no_player(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    (local / "Roblox" / "Versions").mkdir(parents=True)
    blox_dir = _mk_player(
        local / "Bloxstrap" / "Versions", "version-BLOX",
        exe_mtime=time.time(), dir_mtime=time.time())

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: None))

    assert RobloxManager.get_roblox_version_dir() == str(blox_dir)


def test_install_root_is_stock_when_stock_tree_exists(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    stock = local / "Roblox" / "Versions"
    stock.mkdir(parents=True)
    _mk_player(local / "Bloxstrap" / "Versions", "version-BLOX",
               exe_mtime=time.time(), dir_mtime=time.time())

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: None))

    assert RobloxManager.get_install_versions_root() == str(stock)


def test_ensure_stock_versions_root_creates_missing_tree(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    stock = local / "Roblox" / "Versions"
    assert not stock.exists()
    created = RobloxManager.ensure_stock_versions_root()
    assert created == str(stock)
    assert stock.is_dir()


def test_resolve_download_root_creates_stock_when_nothing_installed(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(RobloxManager, "get_running_version_dir",
                        staticmethod(lambda: None))
    root = RobloxManager.resolve_download_versions_root()
    assert root == str(local / "Roblox" / "Versions")
    assert os.path.isdir(root)
