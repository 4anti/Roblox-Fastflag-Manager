import pytest
from src.core.version_changer import manifest


def test_parses_v0_records():
    # Arrange: v0 header + two 4-line records.
    text = (
        "v0\n"
        "RobloxApp.zip\n" + "a" * 32 + "\n" + "100\n" + "200\n"
        "shaders.zip\n" + "b" * 32 + "\n" + "10\n" + "20\n"
    )
    # Act
    pkgs = manifest.parse_manifest(text)
    # Assert
    assert [p["name"] for p in pkgs] == ["RobloxApp.zip", "shaders.zip"]
    assert pkgs[0]["md5"] == "a" * 32
    assert pkgs[0]["packed_size"] == 100
    assert pkgs[0]["size"] == 200


def test_skips_launcher_executable():
    text = (
        "v0\n"
        "RobloxPlayerLauncher.exe\n" + "c" * 32 + "\n" + "5\n" + "5\n"
        "RobloxApp.zip\n" + "a" * 32 + "\n" + "100\n" + "200\n"
    )
    pkgs = manifest.parse_manifest(text)
    assert [p["name"] for p in pkgs] == ["RobloxApp.zip"]


def test_rejects_unknown_header():
    with pytest.raises(ValueError):
        manifest.parse_manifest("v9\nRobloxApp.zip\n" + "a" * 32 + "\n1\n2\n")


def test_rejects_truncated_record():
    # Missing the final size line.
    text = "v0\nRobloxApp.zip\n" + "a" * 32 + "\n100\n"
    with pytest.raises(ValueError):
        manifest.parse_manifest(text)


def test_manifest_url_uses_guid():
    assert manifest.manifest_url("https://setup.rbxcdn.com/", "version-abc") == \
        "https://setup.rbxcdn.com/version-abc-rbxPkgManifest.txt"
