"""Regressions for auth-route boundary hardening.

1. Malformed credential-shaped inputs (device fingerprint, password-reset
   token, 2FA challenge token/code) are refused at the HTTP trust boundary
   rather than reaching token hashing, DB lookups or audit lines.
2. A 2FA challenge cannot be exchanged for a fresh session after the account
   has been locked or frozen in the minutes since the challenge was minted.

Deterministic: no clock, network or external services.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.auth.models import AuthResult, AuthStatus


@pytest.mark.auth
@pytest.mark.api
class TestCredentialInputBoundary:
    def test_login_rejects_control_char_in_device_fingerprint(self, test_client):
        # The fingerprint is stored as a device trust key; a newline/C0 byte
        # must never be persisted or reach an audit line.
        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": "nobody",
                "password": "WrongPassword!",
                "device_fingerprint": "abc\x0adefgh",
            },
        )
        assert response.status_code == 400, response.text
        assert "control characters" in response.text.lower()

    def test_login_accepts_clean_device_fingerprint(self, test_client):
        # A benign fingerprint still reaches the auth core (denied here only
        # because the user does not exist), so the guard is not over-broad.
        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": "nobody",
                "password": "WrongPassword!",
                "device_fingerprint": "abcdefgh1234",
            },
        )
        assert response.status_code == 401, response.text

    def test_password_reset_rejects_control_char_in_token(self, test_client):
        response = test_client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "email.1.abcdefgh\ninjected", "new_password": "NewPass123!"},
        )
        assert response.status_code == 400, response.text
        assert "control characters" in response.text.lower()

    def test_password_reset_rejects_oversized_token(self, test_client):
        response = test_client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "a" * 600, "new_password": "NewPass123!"},
        )
        assert response.status_code == 400, response.text
        assert "maximum length" in response.text.lower()

    def test_2fa_rejects_control_char_in_challenge_token(self, test_client):
        response = test_client.post(
            "/api/v1/auth/2fa",
            json={"challenge_token": "2fa.1.abcdefgh\ninjected", "code": "123456"},
        )
        assert response.status_code == 400, response.text
        assert "control characters" in response.text.lower()


class _Request(dict):
    """Minimal request: works with ``get_client_ip`` and the CSRF-free route."""

    headers = {"user-agent": "test"}


@pytest.mark.auth
@pytest.mark.api
class TestTwoFactorAccountStateRecheck:
    @staticmethod
    def _fake_auth(account_locked, deletion_status="active"):
        fake = MagicMock()
        user = SimpleNamespace(
            id=123,
            username="victim",
            created_at=0,
            account_locked=account_locked,
            deletion_status=deletion_status,
        )
        session = SimpleNamespace(id=456)
        fake.complete_2fa.return_value = AuthResult(
            status=AuthStatus.SUCCESS,
            token="123.secretvalue",
            user=user,
            session=session,
        )
        fake.get_user.return_value = user
        return fake

    @staticmethod
    async def _complete(fake):
        from fastapi.responses import Response

        from src.api.routes.auth.two_factor import complete_2fa
        from src.api.schemas.auth import TwoFactorRequest

        request = _Request(client=("1.2.3.4", 1234))
        response = Response()
        with patch("src.api.routes.auth.two_factor.api.get_auth", return_value=fake):
            await complete_2fa(
                request,
                response,
                TwoFactorRequest(challenge_token="2fa.1.abcdefgh", code="123456"),
            )
        return response

    @pytest.mark.asyncio
    async def test_locked_account_cannot_complete_stale_challenge(self):
        from fastapi import HTTPException

        fake = self._fake_auth(account_locked=True)
        with pytest.raises(HTTPException) as exc:
            await self._complete(fake)
        assert exc.value.status_code == 401
        # The session minted by the core before the refusal must be revoked,
        # and no credential may be attached to the response.
        fake.revoke_session.assert_called_once_with(123, 456)

    @pytest.mark.asyncio
    async def test_frozen_account_cannot_complete_stale_challenge(self):
        from fastapi import HTTPException

        fake = self._fake_auth(account_locked=False, deletion_status="frozen")
        with pytest.raises(HTTPException) as exc:
            await self._complete(fake)
        assert exc.value.status_code == 401
        fake.revoke_session.assert_called_once_with(123, 456)

    @pytest.mark.asyncio
    async def test_active_account_still_completes(self):
        fake = self._fake_auth(account_locked=False)
        response = await self._complete(fake)
        assert any(
            k.lower() == b"set-cookie" for k, _ in response.raw_headers
        ), "a healthy account must still receive the session cookie"
        fake.revoke_session.assert_not_called()
