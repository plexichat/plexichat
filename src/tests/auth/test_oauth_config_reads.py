"""OAuth redirect allow-list must use the per-request config snapshot."""

import importlib

import pytest

pytestmark = pytest.mark.unit


def test_allowlist_uses_passed_snapshot_without_rereading_config(monkeypatch):
    mod = importlib.import_module("src.api.routes.auth.oauth")
    snapshot = {"github": {"allowed_redirect_uris": ["https://app.example.com/cb"]}}

    def forbidden_get(key, default=None):
        raise AssertionError(
            "oauth config must not be re-read when a snapshot is passed"
        )

    monkeypatch.setattr(mod.config_util, "get", forbidden_get)

    assert mod._redirect_uri_is_allowed(
        "github", "https://app.example.com/cb", snapshot
    )
    assert not mod._redirect_uri_is_allowed(
        "github", "https://evil.example/cb", snapshot
    )
