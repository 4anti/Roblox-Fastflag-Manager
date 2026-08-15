import os
import zipfile
from src.core.version_changer import installer


def test_target_subdir_known_package():
    assert installer.target_subdir("RobloxApp.zip") == ""
    assert installer.target_subdir("content-textures2.zip") == "content/textures/"


def test_target_subdir_unknown_package_returns_none():
    assert installer.target_subdir("totally-made-up.zip") is None


def test_extract_places_files_in_mapped_subdir(tmp_path):
    # Arrange: a zip that should land under shaders/
    z = tmp_path / "shaders.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("shader.bin", b"x")
    dest_root = tmp_path / "version-abc"
    # Act
    installer.extract_package(str(z), "shaders.zip", str(dest_root))
    # Assert
    assert (dest_root / "shaders" / "shader.bin").is_file()


def test_write_appsettings_creates_file(tmp_path):
    dest_root = tmp_path / "version-abc"
    dest_root.mkdir()
    installer.write_appsettings(str(dest_root))
    content = (dest_root / "AppSettings.xml").read_text(encoding="utf-8")
    assert "<ContentFolder>content</ContentFolder>" in content


def test_commit_moves_staging_into_versions_root(tmp_path):
    versions_root = tmp_path / "Versions"
    versions_root.mkdir()
    staged = tmp_path / "staged-build"
    staged.mkdir()
    (staged / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    # Act
    final = installer.commit_build(str(staged), str(versions_root), "version-abc")
    # Assert
    assert os.path.isfile(os.path.join(final, "RobloxPlayerBeta.exe"))
    assert final == str(versions_root / "version-abc")


def test_commit_creates_missing_versions_root(tmp_path):
    versions_root = tmp_path / "Roblox" / "Versions"
    staged = tmp_path / "staged-build"
    staged.mkdir()
    (staged / "RobloxPlayerBeta.exe").write_bytes(b"exe")
    final = installer.commit_build(str(staged), str(versions_root), "version-abc")
    assert os.path.isfile(os.path.join(final, "RobloxPlayerBeta.exe"))
    assert os.path.isdir(str(versions_root))


def test_target_subdir_includes_libraries_and_redist():
    # Regression: real player packages that extract to root. They were missing
    # from the map (install would abort on them). Verified against
    # bloxstraplabs/bloxstrap CommonAppData.cs.
    assert installer.target_subdir("Libraries.zip") == ""
    assert installer.target_subdir("redist.zip") == ""
