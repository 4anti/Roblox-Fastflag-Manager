from src.core import offset_loader


def test_fetch_latest_build_returns_embedded_clientversion(monkeypatch):
    # Arrange: one network source yields a body with an embedded ClientVersion.
    body = b'ClientVersion = "version-abc123"\ninline uintptr_t X = 0x1;\n'
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: body)]),
    )

    # Act
    result = offset_loader.fetch_latest_build()

    # Assert
    assert result == "version-abc123"


def test_fetch_latest_build_uses_header_form(monkeypatch):
    # Arrange: body without ClientVersion= but with the header line form.
    body = b'// Roblox Version: version-deadbeef\ninline uintptr_t X = 0x1;\n'
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: body)]),
    )

    # Act / Assert
    assert offset_loader.fetch_latest_build() == "version-deadbeef"


def test_fetch_latest_build_none_when_all_sources_fail(monkeypatch):
    # Arrange: every source returns no body.
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: None)]),
    )

    # Act / Assert
    assert offset_loader.fetch_latest_build() is None


def test_fetch_latest_build_skips_body_without_version(monkeypatch):
    # Arrange: first body has no version, second one does.
    good = b'ClientVersion = "version-second"\n'
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([
            ("a", lambda: b'no version here\n'),
            ("b", lambda: good),
        ]),
    )

    # Act / Assert
    assert offset_loader.fetch_latest_build() == "version-second"


def test_fetch_latest_build_tolerates_non_ascii_byte_in_body(monkeypatch):
    # Regression: the old inline decode used .decode("ascii") with no error
    # handler, which raised UnicodeDecodeError when a non-ASCII byte appeared
    # inside the ClientVersion quoted value, causing the function to skip the
    # version and return None.
    #
    # _extract_imtheo_client_version uses errors="ignore", so it strips the
    # offending byte and returns the version with only the valid ASCII chars.
    #
    # Body has a \xff byte embedded inside the quoted value between two hex
    # runs.  The regex [^"]+ captures b'version-ca\xfefe'; decoding with
    # errors="ignore" drops \xff, yielding "version-cafe".
    body = b'ClientVersion = "version-ca\xfefe"\ninline uintptr_t X = 0x1;\n'
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: body)]),
    )

    result = offset_loader.fetch_latest_build()

    # The non-ASCII byte is silently dropped; valid hex chars are preserved.
    assert result == "version-cafe"


def _synthesize_body(build_version: str, count: int = 600) -> bytes:
    """Build a fake FFlags.hpp body with enough entries to pass the
    MIN_VALID_FLAGS threshold, plus an embedded ClientVersion line."""
    lines = [f'ClientVersion = "{build_version}"\n'.encode("ascii")]
    for i in range(count):
        # RVA >= 0x100000 to pass the range guard in _parse_imtheo_known_names_only.
        rva = 0x200000 + i * 0x10
        lines.append(
            f"inline constexpr uintptr_t FFlagTest{i} = 0x{rva:X};\n".encode("ascii")
        )
    return b"".join(lines)


def test_load_known_flag_names_seeds_last_source_build(monkeypatch):
    """The startup path (flag_manager.load_offsets -> load_known_flag_names)
    must record which Roblox build the offsets target so the frontend's
    version-check card doesn't declare "matches" on empty knowledge."""
    # Arrange: clear any prior state, then feed a network body with an
    # embedded ClientVersion.
    offset_loader.reset_cache()
    body = _synthesize_body("version-startuprecord")
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: body)]),
    )

    # Act
    names = offset_loader.load_known_flag_names()

    # Assert: names returned AND the session-level marker was populated so
    # get_loading_status() can hand the frontend a real answer.
    assert len(names) >= 500
    assert offset_loader.last_source_build() == "version-startuprecord"
    assert offset_loader.last_source_id() == "imtheo_requests"


def test_load_known_flag_names_does_not_clobber_prior_source_build(monkeypatch):
    """load_offsets is authoritative — if it already ran and recorded a build,
    a subsequent load_known_flag_names call (e.g. a UI refresh) must not
    overwrite that value with whatever it happens to fetch."""
    # Arrange: pretend load_offsets already ran with a specific build.
    offset_loader.reset_cache()
    offset_loader._last_source_build = "version-authoritative"
    offset_loader._last_source_id = "load_offsets_prior"
    body = _synthesize_body("version-different")
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: body)]),
    )

    # Act
    offset_loader.load_known_flag_names()

    # Assert: the earlier authoritative value is preserved.
    assert offset_loader.last_source_build() == "version-authoritative"
    assert offset_loader.last_source_id() == "load_offsets_prior"


def test_load_known_flag_names_falls_back_to_disk_cache_build(monkeypatch, tmp_path):
    """When every network source fails, the disk-cache fallback must still
    seed the source build from the cache header so the UI has a real answer
    to compare against the installed Roblox build."""
    # Arrange: no network AND no bundled baseline, but a disk cache with a
    # source_build_version header. Both stubs are needed because
    # `_fetch_body_via_chain` treats the bundled baseline as a network-adjacent
    # last-resort BEFORE returning empty — without stubbing it, the real
    # PyInstaller resource path serves back the shipped FFlags.hpp and the
    # disk-cache branch never runs.
    offset_loader.reset_cache()
    monkeypatch.setattr(
        offset_loader, "_iter_network_sources",
        lambda: iter([("imtheo_requests", lambda: None)]),
    )
    from src.core import offset_sources
    monkeypatch.setattr(offset_sources, "read_bundled_baseline", lambda: None)
    cache_path = tmp_path / "offsets_cache.json"
    cache_path.write_text(
        '{"source_build_version": "version-fromcache", '
        '"flags": {"TestFlag": {"full_name": "FFlagTestFlag", "type": "bool"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(offset_loader, "CACHE_PATH", str(cache_path))
    monkeypatch.setattr(
        offset_loader, "_migrate_legacy_cache_if_needed", lambda: None
    )

    # Act
    names = offset_loader.load_known_flag_names()

    # Assert: the cache header propagated to the session marker.
    assert names == {"FFlagTestFlag": "bool"}
    assert offset_loader.last_source_build() == "version-fromcache"
