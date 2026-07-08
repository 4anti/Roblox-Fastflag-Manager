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
