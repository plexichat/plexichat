"""Agent5 C1 regression: the 2FA challenge flow over REST must stay intact.

Pre-fix, ``AuthStatus.TWO_FACTOR_REQUIRED`` carried the value ``"2fa_required"``
while the login route compared/emitted ``"two_factor_required"``, so the 2FA
branch never matched and a 2FA login fell through to a ``"success"`` body with
a null token — the entire login -> 2FA -> token flow was broken.

These tests fail on any reintroduction of that drift and pass when:
  1. the enum value is unified (``TWO_FACTOR_REQUIRED.value ==
     "two_factor_required"``),
  2. POST /api/v1/auth/login for a 2FA-enabled account returns the challenge
     status with a challenge token (and no session token),
  3. POST /api/v1/auth/2fa rejects a wrong code with 401, and
  4. POST /api/v1/auth/2fa with the correct TOTP completes to ``"success"``
     with an authenticated user.
"""

import uuid
from unittest.mock import patch

import pyotp

from src.core.auth.models import AuthStatus
from src.utils import encryption

PASSWORD = "TestPass123!"


def _register_user(test_client, username):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        r = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": PASSWORD,
            },
        )
    assert r.status_code == 200, r.text


def _enable_2fa(auth_manager, db, username):
    row = db.fetch_one("SELECT id FROM auth_users WHERE username = ?", (username,))
    assert row is not None
    user_id = row["id"]
    setup = auth_manager._get_manager().setup_2fa(user_id)
    totp = pyotp.TOTP(setup.secret)
    assert auth_manager._get_manager().confirm_2fa(user_id, totp.now()) is True
    return totp


def _setup_2fa_user(test_client, auth_manager, db, label="c1"):
    username = f"{label}_{uuid.uuid4().hex[:8]}"
    _register_user(test_client, username)
    totp = _enable_2fa(auth_manager, db, username)
    return username, totp


class TestTwoFactorEnumUnified:
    def test_enum_value_matches_rest_contract(self):
        # The REST contract (login route, clients) uses "two_factor_required".
        # If the enum ever drifts back to "2fa_required", this fails first.
        assert AuthStatus.TWO_FACTOR_REQUIRED.value == "two_factor_required"
        assert AuthStatus.TWO_FACTOR_REQUIRED.value != "2fa_required"


class TestTwoFactorRestFlow:
    def test_login_returns_challenge_status(self, test_client, auth_manager, db):
        username, _ = _setup_2fa_user(test_client, auth_manager, db)
        with patch.object(encryption, "verify_password", return_value=True):
            r = test_client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": PASSWORD},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # Must be the challenge — never a hollow "success" with a null token.
        assert body["status"] == AuthStatus.TWO_FACTOR_REQUIRED.value
        assert body["status"] == "two_factor_required"
        assert body.get("token") is None
        assert body.get("user") is None
        assert body.get("challenge_token"), "2FA login must issue a challenge token"
        assert body.get("methods") and "totp" in body["methods"]

    def test_verify_wrong_code_rejected(self, test_client, auth_manager, db):
        username, _ = _setup_2fa_user(test_client, auth_manager, db, label="c1bad")
        with patch.object(encryption, "verify_password", return_value=True):
            login = test_client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": PASSWORD},
            )
        assert login.status_code == 200, login.text
        challenge = login.json()["challenge_token"]
        assert challenge

        r = test_client.post(
            "/api/v1/auth/2fa",
            json={"challenge_token": challenge, "code": "000000"},
        )
        assert r.status_code == 401, r.text

    def test_verify_correct_code_succeeds(self, test_client, auth_manager, db):
        username, totp = _setup_2fa_user(test_client, auth_manager, db, label="c1ok")
        with patch.object(encryption, "verify_password", return_value=True):
            login = test_client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": PASSWORD},
            )
        assert login.status_code == 200, login.text
        challenge = login.json()["challenge_token"]
        assert challenge

        r = test_client.post(
            "/api/v1/auth/2fa",
            json={"challenge_token": challenge, "code": totp.now()},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == AuthStatus.SUCCESS.value
        assert body["status"] == "success"
        assert body.get("user", {}).get("username") == username
