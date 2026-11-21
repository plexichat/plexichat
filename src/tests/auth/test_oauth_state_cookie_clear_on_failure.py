"""The OAuth state-binding cookie must be cleared on every failed callback.

The callback sets ``OAUTH_STATE_COOKIE`` at login-init and the login-CSRF gate
requires it to match the presented state. When a callback is rejected, the
handler calls ``response.delete_cookie(...)`` and then raises ``HTTPException``.
FastAPI discards the injected ``Response`` when an exception is raised (the app's
custom ``HTTPException`` handler builds a brand-new response from the exception),
so the deletion ``Set-Cookie`` header never reached the browser. The binding
cookie therefore survived the failed flow for its full TTL.

These regressions exercise the real ASGI app so the assertion is on the actual
response headers, not on the (discarded) injected Response object.
"""

import pytest

pytestmark = pytest.mark.auth

_STATE_COOKIE = "plexichat_oauth_state"


def _oauth_config(key, default=None):
    if key == "oauth":
        return {
            "google": {"client_id": "test-client"},
            "allow_localhost_redirects": True,
        }
    return default


def _login_init(test_client):
    return test_client.get(
        "/api/v1/auth/oauth/google/login",
        params={
            "redirect_uri": "http://localhost:8000",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )


def _delete_header(response):
    """Return the Set-Cookie headers that clear the state cookie."""
    return [
        value
        for value in response.headers.get_list("set-cookie")
        if value.lower().startswith(f"{_STATE_COOKIE}=")
    ]


def test_cookie_mismatch_failure_clears_state_cookie(test_client, monkeypatch):
    monkeypatch.setattr("src.api.routes.auth.oauth.config_util.get", _oauth_config)

    init = _login_init(test_client)
    assert init.status_code == 200, init.text
    state = init.json()["state"]
    assert _STATE_COOKIE in test_client.cookies

    # Simulate the browser that did not start *this* flow: login-CSRF rejection
    # happens on the missing/mismatching cookie, with a genuine server state.
    test_client.cookies.clear()
    result = test_client.post(
        "/api/v1/auth/oauth/google/callback",
        json={
            "code": "whatever",
            "state": state,
            "redirect_uri": "http://localhost:8000",
            "code_verifier": "b" * 43,
        },
    )

    assert result.status_code == 400, result.text
    assert "state cookie" in result.json()["error"]["message"]
    headers = _delete_header(result)
    assert headers, "failed callback must send a Set-Cookie clearing the state cookie"
    assert "Max-Age=0" in headers[0]
    assert f"{_STATE_COOKIE}=" in headers[0]


def test_invalid_state_failure_clears_state_cookie(test_client, monkeypatch):
    monkeypatch.setattr("src.api.routes.auth.oauth.config_util.get", _oauth_config)

    assert _login_init(test_client).status_code == 200

    result = test_client.post(
        "/api/v1/auth/oauth/google/callback",
        json={
            "code": "whatever",
            "state": "forged-state-value",
            "redirect_uri": "http://localhost:8000",
            "code_verifier": "b" * 43,
        },
    )

    assert result.status_code == 400, result.text
    headers = _delete_header(result)
    assert headers, "failed callback must send a Set-Cookie clearing the state cookie"
    assert "Max-Age=0" in headers[0]
