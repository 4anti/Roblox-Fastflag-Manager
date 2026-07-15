import pytest
from src.core.version_changer import deployment


@pytest.fixture(autouse=True)
def _reset_cache_between_tests():
    """v4.0.4 added a module-level TTL cache in deployment. Reset it
    before each test so cross-test state doesn't leak."""
    deployment._cache_reset()
    yield
    deployment._cache_reset()

def test_parses_clientversionupload_from_json(monkeypatch):
    # Arrange: primary JSON endpoint returns a clientVersionUpload field.
    monkeypatch.setattr(deployment, "_http_get_json",
                        lambda url: {"clientVersionUpload": "version-aaaa1111"})
    monkeypatch.setattr(deployment, "_http_get_text", lambda url: None)
    # Act / Assert
    assert deployment.get_latest_production_guid() == "version-aaaa1111"


def test_falls_back_to_setup_version_text(monkeypatch):
    # Arrange: JSON endpoint fails, plain-text /version endpoint works.
    monkeypatch.setattr(deployment, "_http_get_json", lambda url: None)
    monkeypatch.setattr(deployment, "_http_get_text",
                        lambda url: "version-bbbb2222\n")
    # Act / Assert
    assert deployment.get_latest_production_guid() == "version-bbbb2222"


def test_returns_none_when_all_sources_fail(monkeypatch):
    monkeypatch.setattr(deployment, "_http_get_json", lambda url: None)
    monkeypatch.setattr(deployment, "_http_get_text", lambda url: None)
    assert deployment.get_latest_production_guid() is None


def test_rejects_non_version_strings(monkeypatch):
    # Arrange: a captive-portal style HTML body must not be accepted.
    monkeypatch.setattr(deployment, "_http_get_json", lambda url: None)
    monkeypatch.setattr(deployment, "_http_get_text",
                        lambda url: "<html>blocked</html>")
    assert deployment.get_latest_production_guid() is None
