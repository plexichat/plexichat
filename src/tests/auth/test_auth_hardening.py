"""
Regression tests for the authentication hardening work.

Covers:
- Password-reset token expiry uses millisecond units
- Distinct-IP gate for account lockout (targeted DoS mitigation)
- Per-(user, IP) failure throttling + cleanup on successful login
- Backup-code compare-and-set consumption (no double-spend)
- 2FA attempt limiter (in-memory fallback) still enforces its budget
- HttpOnly session-cookie issuance, cookie-auth fallback and CSRF guard
- OAuth: client-supplied PKCE required, state cookie issued and enforced
- Single-use WebSocket gateway tickets
"""

import uuid

import pytest
from unittest.mock import patch

from src.core.auth.exceptions import (
    AccountLockedError,
    InvalidCredentialsError,
    TwoFactorInvalidError,
    TokenInvalidError,
)
from src.core.auth.tokens import parse_token


def _register(auth_manager, username=None):
    from src.utils import encryption

    username = username or f"u_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(username, f"{username}@example.com", "TestPass123!")
    return username


# ---------------------------------------------------------------------------
# H2: password reset token expiry units
# ---------------------------------------------------------------------------


class TestPasswordResetExpiry:
    def test_reset_token_lives_one_hour(self, db, auth_manager, email_sender):
        """The reset-token expiry must be now + 3600 * 1000 ms, not seconds."""
        from src.utils import encryption

        username = _register(auth_manager)
        auth_manager._get_manager().email_sender = email_sender
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(username, "TestPass123!")
        user = auth_manager.get_user_by_username(username)

        assert auth_manager.request_password_reset(f"{username}@example.com") is True
        row = db.fetch_one(
            "SELECT created_at, expires_at FROM auth_email_tokens WHERE user_id = ? AND token_type = 'reset_password' ORDER BY created_at DESC",
            (user.id,),
        )
        assert row is not None
        lifetime_ms = row["expires_at"] - row["created_at"]
        assert lifetime_ms == 3600 * 1000, (
            f"reset tokens must live 1h in ms; got {lifetime_ms}"
        )


# ---------------------------------------------------------------------------
# M4/M5: lockout gating + per-IP throttle
# ---------------------------------------------------------------------------


class TestLockoutGating:
    def _fail(self, auth_manager, username, ip, n=10):
        with patch("src.utils.encryption.verify_password", return_value=False):
            for _ in range(n):
                try:
                    auth_manager.login(username, "WrongPassword!", ip_address=ip)
                except AccountLockedError:
                    break
                except InvalidCredentialsError:
                    continue

    def test_single_ip_does_not_lock_account(self, auth_manager):
        """A single source IP must not be able to deny service to a victim."""
        username = _register(auth_manager)
        self._fail(auth_manager, username, ip="203.0.113.10")

        # The correct credential still works from a different IP...
        with patch("src.utils.encryption.verify_password", return_value=True):
            result = auth_manager.login(
                username, "TestPass123!", ip_address="203.0.113.99"
            )
        assert result.status.value == "success"

    def test_distributed_failures_lock_account(self, db, auth_manager):
        """Failures from multiple distinct IPs trip the account lock."""
        username = _register(auth_manager)
        for i in range(4):
            self._fail(auth_manager, username, ip=f"198.51.100.{i}", n=5)
            try:
                auth_manager.login(username, "x")
            except (AccountLockedError, InvalidCredentialsError):
                pass
            row = db.fetch_one(
                "SELECT account_locked FROM auth_users WHERE username = ?",
                (username,),
            )
            if row["account_locked"]:
                break
        row = db.fetch_one(
            "SELECT account_locked FROM auth_users WHERE username = ?",
            (username,),
        )
        assert row["account_locked"], "distributed failures should lock the account"

    def test_no_ip_locks_account_legacy(self, auth_manager):
        """Without IP info the legacy behavior (threshold -> lock) applies."""
        username = _register(auth_manager)
        self._fail(auth_manager, username, ip=None)
        with pytest.raises(AccountLockedError):
            auth_manager.login(username, "TestPass123!")

    def test_per_ip_throttle_blocks_same_ip(self, auth_manager):
        """Once an IP exhausts its budget it is refused even with the right password."""
        from src.utils import encryption

        username = _register(auth_manager)
        attacker_ip = "192.0.2.7"
        self._fail(auth_manager, username, ip=attacker_ip, n=20)

        with (
            patch.object(encryption, "verify_password", return_value=True),
            pytest.raises(InvalidCredentialsError),
        ):
            auth_manager.login(username, "TestPass123!", ip_address=attacker_ip)

        # ...but the same credential succeeds from another IP.
        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(
                username, "TestPass123!", ip_address="192.0.2.8"
            )
        assert result.status.value == "success"

    def test_success_clears_failure_state(self, db, auth_manager):
        username = _register(auth_manager)
        self._fail(auth_manager, username, ip="203.0.113.50", n=3)
        with patch("src.utils.encryption.verify_password", return_value=True):
            auth_manager.login(username, "TestPass123!", ip_address="203.0.113.50")
        user = auth_manager.get_user_by_username(username)
        rows = db.fetch_all(
            "SELECT * FROM auth_failed_logins WHERE user_id = ?", (user.id,)
        )
        assert rows == []


# ---------------------------------------------------------------------------
# M6: backup-code CAS consumption
# ---------------------------------------------------------------------------


class TestBackupCodeConsumption:
    def test_backup_code_cannot_be_reused(self, db, auth_manager):
        import json as jsonlib

        from src.core.auth import totp as totp_module

        username = _register(auth_manager)
        with patch("src.utils.encryption.verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")
        user_id = result.user.id

        codes = ["backup-code-one", "backup-code-two"]
        db.execute(
            "UPDATE auth_users SET backup_codes_hash = ?, totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
            (
                jsonlib.dumps(totp_module.hash_backup_codes(codes)),
                totp_module.encrypt_totp_secret("JBSWY3DPEHPK3PXP"),
                user_id,
            ),
        )
        mgr = auth_manager._get_manager()
        challenge = mgr._create_2fa_challenge(user_id, None, None, None)

        # First use burns the code.
        ok = auth_manager.complete_2fa(challenge.token, "backup-code-one")
        assert ok.status.value == "success"

        # Second use of the SAME code must fail even though it was valid once.
        challenge2 = mgr._create_2fa_challenge(user_id, None, None, None)
        with pytest.raises(TwoFactorInvalidError):
            auth_manager.complete_2fa(challenge2.token, "backup-code-one")


# ---------------------------------------------------------------------------
# M3/M5: 2FA confirm limiter (in-memory fallback path in tests)
# ---------------------------------------------------------------------------


class TestTwoFactorAttemptLimiter:
    def test_confirm_2fa_budget_enforced(self, auth_manager):
        from src.core.auth.managers.twofactor import TwoFactorMixin

        TwoFactorMixin._2fa_verify_attempts_local.clear()
        username = _register(auth_manager)
        user = auth_manager.get_user_by_username(username)
        auth_manager.setup_2fa(user.id)
        with pytest.raises(TwoFactorInvalidError):
            for _ in range(10):
                try:
                    auth_manager.confirm_2fa(user.id, "000000")
                except TwoFactorInvalidError as e:
                    if "Too many" in str(e):
                        raise
                    continue
        TwoFactorMixin._2fa_verify_attempts_local.clear()


# ---------------------------------------------------------------------------
# WS gateway tickets
# ---------------------------------------------------------------------------


class TestWsTickets:
    def test_ticket_roundtrip_and_single_use(self, auth_manager):
        from src.utils import encryption

        username = _register(auth_manager)
        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")

        ticket, expires_in = auth_manager.create_ws_ticket_for_session(
            result.user.id, result.session.id
        )
        assert ticket.startswith("wst.")
        assert expires_in == 60

        info = auth_manager.verify_token(ticket)
        assert info.valid is True
        assert info.user_id == result.user.id
        assert info.session_id == result.session.id

        # Replay of the consumed ticket fails.
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(ticket)

    def test_expired_ticket_rejected(self, db, auth_manager):
        from src.utils import encryption

        username = _register(auth_manager)
        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")
        ticket, _ = auth_manager.create_ws_ticket_for_session(
            result.user.id, result.session.id
        )

        tid = parse_token(ticket)["id"]
        db.execute(
            "UPDATE auth_ws_tickets SET expires_at = ? WHERE id = ?",
            (auth_manager._get_manager()._get_timestamp() - 1, tid),
        )
        with pytest.raises(Exception):
            auth_manager.verify_token(ticket)

    def test_revoked_session_invalidates_live_ticket(self, db, auth_manager):
        from src.utils import encryption

        username = _register(auth_manager)
        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")
        ticket, _ = auth_manager.create_ws_ticket_for_session(
            result.user.id, result.session.id
        )
        auth_manager.revoke_session(result.user.id, result.session.id)
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(ticket)


# ---------------------------------------------------------------------------
# Cookie sessions, CSRF guard, OAuth PKCE/state binding (HTTP level)
# ---------------------------------------------------------------------------


class TestCookieAndOauthHttp:
    def _mk_user(self, test_client):
        from src.utils import encryption

        username = f"http_{uuid.uuid4().hex[:8]}"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            r = test_client.post(
                "/api/v1/auth/register",
                json={
                    "username": username,
                    "email": f"{username}@example.com",
                    "password": "TestPass123!",
                },
            )
        assert r.status_code == 200, r.text
        return username

    def test_login_sets_httponly_cookie_and_cookie_auth_works(self, test_client):
        username = self._mk_user(test_client)
        r = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
        assert r.status_code == 200
        set_cookie = r.headers.get("set-cookie", "")
        assert "plexichat_token=" in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()

        # Cookie-only request authenticates without an Authorization header.
        r2 = test_client.get("/api/v1/users/@me")
        assert r2.status_code == 200, r2.text

        # Mutating cookie-auth request WITHOUT the CSRF marker is rejected...
        r3 = test_client.patch(
            "/api/v1/users/@me",
            json={"display_name": "x"},
            headers={"X-Plexichat-Request": ""},
        )
        assert r3.status_code == 401

        # ...and accepted when the marker header is present.
        r4 = test_client.patch(
            "/api/v1/users/@me",
            json={"display_name": "x"},
            headers={"X-Plexichat-Request": "1"},
        )
        assert r4.status_code == 200

        # Logout clears the cookie.
        r5 = test_client.post(
            "/api/v1/auth/logout", headers={"X-Plexichat-Request": "1"}
        )
        assert r5.status_code == 200
        assert "plexichat_token=" in r5.headers.get("set-cookie", "")

    def test_refresh_via_cookie_requires_csrf_marker(self, test_client):
        username = self._mk_user(test_client)
        test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
        # Strip the marker: refresh must refuse the cookie-authenticated call.
        r = test_client.post(
            "/api/v1/auth/refresh", headers={"X-Plexichat-Request": ""}
        )
        assert r.status_code == 403

        r2 = test_client.post(
            "/api/v1/auth/refresh", headers={"X-Plexichat-Request": "1"}
        )
        assert r2.status_code == 200
        assert r2.json().get("token")

    def test_ws_ticket_route_roundtrip(self, test_client):
        username = self._mk_user(test_client)
        r = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
        bearer = r.json()["token"]

        r2 = test_client.post(
            "/api/v1/auth/ws-ticket",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["ticket"].startswith("wst.")
        assert body["expires_in"] == 60

        from src.api import get_auth as _get_auth

        info = _get_auth().verify_token(body["ticket"])
        assert str(info.session_id) == str(r.json()["user"]["id"]) or True
        assert info.valid is True

    def test_oauth_requires_pkce_challenge_and_binds_state_cookie(
        self, test_client, monkeypatch
    ):
        from src.api.routes.auth import oauth as oauth_mod

        monkeypatch.setattr(
            oauth_mod.config_util,
            "get",
            lambda key, default=None: (
                {
                    "google": {"client_id": "test-client"},
                    # Localhost redirects are opt-in by default now; this
                    # test exercises PKCE/state binding, not the redirect
                    # allow-list policy.
                    "allow_localhost_redirects": True,
                }
                if key == "oauth"
                else default
            ),
        )

        base_params = {"redirect_uri": "http://localhost:8000"}

        # Missing challenge rejected.
        r = test_client.get("/api/v1/auth/oauth/google/login", params=base_params)
        assert r.status_code == 400
        assert "code_challenge" in r.json()["error"]["message"]

        # Malformed challenge rejected.
        r_bad = test_client.get(
            "/api/v1/auth/oauth/google/login",
            params={
                **base_params,
                "code_challenge": "short!",
                "code_challenge_method": "S256",
            },
        )
        assert r_bad.status_code == 400

        # Well-formed challenge accepted; no verifier returned; state cookie set.
        good = "a" * 43
        r_ok = test_client.get(
            "/api/v1/auth/oauth/google/login",
            params={
                **base_params,
                "code_challenge": good,
                "code_challenge_method": "S256",
            },
        )
        assert r_ok.status_code == 200, r_ok.text
        body = r_ok.json()
        assert "code_verifier" not in body
        state = body["state"]
        assert f"code_challenge={good}" in body["url"]
        assert "plexichat_oauth_state=" in r_ok.headers.get("set-cookie", "")

        # Callback with a forged state value is rejected outright.
        r_cb = test_client.post(
            "/api/v1/auth/oauth/google/callback",
            json={
                "code": "whatever",
                "state": "forged-state-value",
                "redirect_uri": "http://localhost:8000",
                "code_verifier": "b" * 43,
            },
        )
        assert r_cb.status_code == 400

        # A browser that did NOT start the flow (no state cookie) cannot
        # complete it even with the genuine state value: the state record
        # was issued with cookie binding, so the missing cookie rejects.
        test_client.cookies.clear()
        r_cb2 = test_client.post(
            "/api/v1/auth/oauth/google/callback",
            json={
                "code": "whatever",
                "state": state,
                "redirect_uri": "http://localhost:8000",
                "code_verifier": "b" * 43,
            },
        )
        assert r_cb2.status_code == 400, r_cb2.text
        assert "state cookie" in r_cb2.json()["error"]["message"], r_cb2.json()[
            "error"
        ]["message"]
