"""Regression: error-response CORS must follow the hardened API policy.

The old ``_get_cors_headers`` defaulted an omitted ``cors_origins`` to
``["*"]`` and reflected any configured value verbatim. With the default
``cors_allow_credentials=True`` this emitted the forbidden
``Access-Control-Allow-Origin: *`` + ``Access-Control-Allow-Credentials: true``
combination on 4xx/5xx responses, and ignored the build-time wildcard
fail-closed rule entirely.
"""

import utils.config as config
from src.api.middleware.error_handling import _get_cors_headers


class _Req:
    def __init__(self, origin=None):
        self.headers = {"origin": origin} if origin is not None else {}


def test_omitted_allowlist_never_grants_wildcard_credentials(monkeypatch):
    # No api.cors_origins configured: a literal '*' Origin must get nothing.
    monkeypatch.setattr(config, "get", lambda *a, **k: {})
    headers = _get_cors_headers(_Req("*"))
    assert "Access-Control-Allow-Origin" not in headers
    assert "Access-Control-Allow-Credentials" not in headers


def test_wildcard_with_credentials_fails_closed(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda *a, **k: {
            "cors_origins": ["*"],
            "cors_allow_credentials": True,
        },
    )
    assert _get_cors_headers(_Req("https://evil.example")) == {}


def test_wildcard_without_credentials_is_anonymous_only(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda *a, **k: {
            "cors_origins": ["*"],
            "cors_allow_credentials": False,
            "allow_wildcard_cors": True,
        },
    )
    headers = _get_cors_headers(_Req("https://any.example"))
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "Access-Control-Allow-Credentials" not in headers


def test_explicit_origin_honours_credential_policy_with_trailing_slash(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda *a, **k: {
            "cors_origins": ["https://good.example/"],
            "cors_allow_credentials": False,
        },
    )
    headers = _get_cors_headers(_Req("https://good.example"))
    assert headers["Access-Control-Allow-Origin"] == "https://good.example"
    assert "Access-Control-Allow-Credentials" not in headers


def test_unlisted_origin_gets_no_cors_headers(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda *a, **k: {
            "cors_origins": ["https://good.example"],
            "cors_allow_credentials": True,
        },
    )
    assert _get_cors_headers(_Req("https://evil.example")) == {}
