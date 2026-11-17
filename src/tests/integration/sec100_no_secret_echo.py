"""SEC100 - end-to-end invariant: no endpoint reflects credential material.

Using the in-process ``TestClient`` this suite plants distinctive canary
secrets (a password and session tokens) and verifies that a sample of
endpoints - success and failure paths alike - never copies those secrets into
a response body.  This is the E2E counterpart of the unit-level redaction
tests: it checks the bytes that actually leave the ASGI app.

Only deterministic body-substring assertions are used; no timing checks.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from src.utils import encryption

# Unique per-run but fixed for the lifetime of the test, so a reflection is
# unambiguous to detect.
_CANARY_PASSWORD = "Canary!Pw-9xLmQ#zzz"
_CANARY_BOGUS_TOKEN = "CANARY_BOGUS_TOKEN_9xLmQ"


def _register(test_client, username: str):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return test_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": _CANARY_PASSWORD,
            },
        )


def _new_username() -> str:
    return f"sec100_{uuid.uuid4().hex[:10]}"


def test_registration_does_not_echo_password(test_client):
    response = _register(test_client, _new_username())
    assert response.status_code == 200, response.text
    assert _CANARY_PASSWORD not in response.text


def test_login_does_not_echo_password_or_session_token(test_client):
    username = _new_username()
    assert _register(test_client, username).status_code == 200
    test_client.cookies.clear()

    with patch.object(encryption, "verify_password", return_value=True):
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": _CANARY_PASSWORD},
        )
    assert response.status_code == 200, response.text
    assert _CANARY_PASSWORD not in response.text

    token = test_client.cookies.get("plexichat_token")
    assert token, "login was expected to set the session cookie"
    assert token not in response.text


def test_authenticated_endpoint_does_not_echo_bearer_token(
    test_client, test_user_with_token
):
    token = test_user_with_token["token"]
    headers = {"Authorization": f"Bearer {token}"}
    for path in (
        "/api/v1/users/@me",
        "/api/v1/health",
    ):
        response = test_client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert token not in response.text, f"{path} reflected the bearer token"


def test_auth_failure_does_not_echo_canary_token(test_client):
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": f"Bearer {_CANARY_BOGUS_TOKEN}"},
    )
    assert response.status_code in (401, 403), response.text
    assert _CANARY_BOGUS_TOKEN not in response.text


def test_failed_login_does_not_echo_submitted_password(test_client):
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": "definitely-not-a-user", "password": _CANARY_PASSWORD},
    )
    assert 400 <= response.status_code < 500, response.text
    assert _CANARY_PASSWORD not in response.text
