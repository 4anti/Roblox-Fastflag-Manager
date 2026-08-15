"""Leftover stock version folders must be removed only after production is a
complete player build, and never from a third-party launcher tree."""
import os

from src.core.version_changer import fixer
from src.core.roblox_manager import RobloxManager


def _player(dirpath):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "RobloxPlayerBeta.exe").write_bytes(b"exe")


def test_prune_deletes_other_complete_folders_when_production_is_complete(tmp_path):
    keep = tmp_path / "version-prod"
    leftover = tmp_path / "version-old"
    _player(keep)
    _player(leftover)
    (tmp_path / "notes.txt").write_text("leave me")

    result = fixer.prune_non_production_builds(str(tmp_path), "version-prod")

    assert os.path.isdir(keep)
    assert not os.path.exists(leftover)
    assert os.path.isfile(tmp_path / "notes.txt")
    assert result["removed"] == ["version-old"]
    assert result["failed"] == []
    assert result["kept"] == str(keep)


def test_prune_accepts_bare_guid(tmp_path):
    keep = tmp_path / "version-abc"
    leftover = tmp_path / "version-zzz"
    _player(keep)
    _player(leftover)

    result = fixer.prune_non_production_builds(str(tmp_path), "abc")

    assert os.path.isdir(keep)
    assert not os.path.exists(leftover)
    assert result["removed"] == ["version-zzz"]


def test_prune_does_nothing_when_production_has_no_player_exe(tmp_path):
    keep = tmp_path / "version-prod"
    leftover = tmp_path / "version-old"
    keep.mkdir()
    _player(leftover)

    result = fixer.prune_non_production_builds(str(tmp_path), "version-prod")

    assert os.path.isdir(leftover)
    assert result["removed"] == []
    assert result["kept"] is None


def test_prune_records_failed_delete(tmp_path, monkeypatch):
    keep = tmp_path / "version-prod"
    leftover = tmp_path / "version-old"
    _player(keep)
    _player(leftover)

    monkeypatch.setattr(
        fixer, "_remove_leftover_build",
        lambda path: "access denied" if os.path.basename(path) == "version-old" else None,
    )

    result = fixer.prune_non_production_builds(str(tmp_path), "version-prod")

    assert "version-old" in result["failed"]
    assert result["removed"] == []


def test_prune_removes_root_launcher_next_to_versions(tmp_path):
    keep = tmp_path / "version-prod"
    _player(keep)
    launcher = tmp_path / "RobloxPlayerLauncher.exe"
    launcher.write_bytes(b"launcher")

    result = fixer.prune_non_production_builds(str(tmp_path), "version-prod")

    assert not os.path.exists(launcher)
    assert "RobloxPlayerLauncher.exe" in result["removed"]


def test_prune_stock_uses_stock_root_only(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    stock = local / "Roblox" / "Versions"
    blox = local / "Bloxstrap" / "Versions"
    _player(stock / "version-prod")
    _player(stock / "version-old")
    _player(blox / "version-old")
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    fixer.prune_stock_non_production("version-prod")

    assert os.path.isdir(stock / "version-prod")
    assert not os.path.exists(stock / "version-old")
    assert os.path.isdir(blox / "version-old")


def test_prune_stock_noop_when_stock_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "missing-local"))
    result = fixer.prune_stock_non_production("version-prod")
    assert result["removed"] == []
    assert result["kept"] is None


def test_get_stock_versions_root_unaffected(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert RobloxManager.get_stock_versions_root() == str(tmp_path / "Roblox" / "Versions")
