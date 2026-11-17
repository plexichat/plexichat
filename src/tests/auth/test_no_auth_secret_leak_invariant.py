"""Security invariant: auth secrets never leak into logs or HTTP responses.

Two independent properties are locked in here:

1. The logging sanitizer masks token/password/secret-shaped values.  A minted
   session credential fed through ``mask_string`` / ``sanitize_log_message`` /
   ``sanitize_data`` must not survive in the emitted text.
2. The auth HTTP responses never echo the credential that was just issued.
   Cookie-flow register/login responses carry only a status marker, never the
   raw session token in the JSON body (the bearer opt-in is the only path that
   returns a token, and it is not exercised here).

A static guard additionally checks that the auth routes and the authentication
middleware mask credential-shaped variables before logging them.

No clock, network or external services: deterministic.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.auth import tokens
from utils.logger import mask_string, sanitize_data, sanitize_log_message

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTH_DIR = _REPO_ROOT / "src" / "api" / "routes" / "auth"
_MIDDLEWARE = _REPO_ROOT / "src" / "api" / "middleware" / "authentication.py"


@pytest.mark.security
@pytest.mark.unit
def test_mask_string_destroys_session_secret():
    secret = tokens.generate_token_secret(32)
    masked = mask_string(secret)
    assert secret not in masked
    assert masked != secret
    assert "***" in masked  # the sanitizer's masking marker is present


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "Authorization: Bearer {secret}",
        "bearer {secret}",
        'token="{secret}"',
        "session_id={secret}",
        "client_secret: {secret}",
    ],
)
def test_sanitize_log_message_masks_token_shaped_values(message):
    secret = tokens.generate_token_secret(32)
    cleaned = sanitize_log_message(message.format(secret=secret))
    assert secret not in cleaned, cleaned


@pytest.mark.security
@pytest.mark.unit
def test_sanitize_data_masks_sensitive_keys():
    secret = tokens.generate_token_secret(32)
    data = {
        "access_token": secret,
        "password": secret,
        "nested": [{"refresh_token": secret}],
        "safe": "hello",
    }
    cleaned = sanitize_data(data)
    assert secret not in str(cleaned)
    assert cleaned["safe"] == "hello"


@pytest.mark.security
@pytest.mark.unit
def test_auth_sources_mask_credentials_before_logging():
    """No auth source logs a raw token/password/secret-shaped f-string var."""
    sources = sorted(_AUTH_DIR.glob("*.py")) + [_MIDDLEWARE]
    banned = {"token", "password", "secret", "auth_header", "credential"}
    offenders: list[str] = []
    for src in sources:
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"debug", "info", "warning", "error", "critical", "exception"}
            ):
                continue
            for arg in ast.walk(node):
                if not isinstance(arg, ast.JoinedStr):
                    continue
                for part in arg.values:
                    if not isinstance(part, ast.FormattedValue):
                        continue
                    name = ast.unparse(part.value)
                    if name in banned:
                        offenders.append(f"{src.name}:{node.lineno}:{name}")
    assert offenders == [], offenders


@pytest.mark.security
@pytest.mark.api
def test_register_response_never_echoes_session_secret(test_client):
    """The exact credential set in the cookie must not appear in the body."""
    from src.utils import encryption

    username = f"leak_{uuid.uuid4().hex[:10]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": "TestPass123!",
            },
        )
    assert response.status_code == 200, response.text
    cookie_token = test_client.cookies.get("plexichat_token")
    assert cookie_token and len(cookie_token) >= 16
    assert cookie_token not in response.text
    assert response.json().get("token") is None


@pytest.mark.security
@pytest.mark.api
def test_login_response_never_echoes_session_secret(test_client):
    """Same invariant on the login cookie flow."""
    from src.utils import encryption

    username = f"leak_{uuid.uuid4().hex[:10]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        created = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": "TestPass123!",
            },
        )
    assert created.status_code == 200, created.text
    test_client.cookies.clear()

    with patch.object(encryption, "verify_password", return_value=True):
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
    assert response.status_code == 200, response.text
    cookie_token = test_client.cookies.get("plexichat_token")
    assert cookie_token and len(cookie_token) >= 16
    assert cookie_token not in response.text
    assert response.json().get("token") is None
