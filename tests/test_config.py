"""Unit tests for bio_toolkit.config (no network, no real secrets)."""

import pytest

from bio_toolkit import config


def test_zotero_group_id():
    assert config.ZOTERO_GROUP_ID == "6432168"


def test_endpoints_present_and_https():
    assert "panelapp" in config.API_ENDPOINTS
    assert "openalex" in config.API_ENDPOINTS
    assert all(url.startswith("https://") for url in config.API_ENDPOINTS.values())


def test_zotero_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ZOTERO_API_KEY", "test-key-123")
    assert config.zotero_api_key() == "test-key-123"


def test_zotero_api_key_missing_raises(monkeypatch):
    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    # If no env var and no secret file, it must raise (not return "").
    secret = config.Path(config.__file__).with_name("zotero_api_key.secret")
    if secret.exists():
        pytest.skip("a local zotero_api_key.secret exists; raise-path not exercised")
    with pytest.raises(config.MissingSecretError):
        config.zotero_api_key()
