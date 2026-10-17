"""
Regression tests for auth cluster fixes.

Covers:
- Registration race surfaces as a 409-style error, not a 500 (M4)
- Profile email updates validate the address format (M4)
- update_user rejects wildcard/unknown permission keys (M5)
- OAuth callback validates the id_token nonce (M6)
- Password reset emails carry a deep link, not just a bare token (L8)
"""

import base64
import json as _json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_id_token(claims):
    """Build a structurally-valid unsigned JWT with the given claims."""
    header = base64.urlsafe_b64encode(
        _json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(_json.dumps(claims).encode()).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.sig"


class TestRegistrationRace:
    """M4: unique-constraint races must map to a client error, not a 500."""

    def test_integrity_error_maps_to_user_exists(self, auth_manager, test_db):
        from src.core.auth.exceptions import UserExistsError
        from src.core.auth.manager import AuthManager

        manager = AuthManager.get_instance()
        orig_fetch = manager._db.fetch_one
        orig_exec = manager._db.execute

        def fake_fetch_one(sql, params=None):
            if "LOWER(username)" in sql or "email_index" in sql:
                return None
            return orig_fetch(sql, params)

        def fake_execute(sql, params=None, **kwargs):
            if "INSERT INTO auth_users" in sql:
                raise sqlite3.IntegrityError(
                    "UNIQUE constraint failed: auth_users.username"
                )
            return orig_exec(sql, params, **kwargs)

        with (
            patch.object(manager._db, "fetch_one", side_effect=fake_fetch_one),
            patch.object(manager._db, "execute", side_effect=fake_execute),
        ):
            with pytest.raises(UserExistsError):
                auth_manager.register(
                    username="racer_1",
                    email="racer_1@example.com",
                    password="TestPass123!",
                )


class TestProfileEmailValidation:
    """M4: profile updates must validate email format like registration."""

    def test_invalid_email_rejected(self, auth_manager, test_user):
        from src.core.auth.exceptions import InvalidEmailError

        with pytest.raises(InvalidEmailError):
            auth_manager.update_user(test_user.id, email="not-an-email")


class TestPermissionDictValidation:
    """M5: update_user must never persist wildcard or unknown permissions."""

    def test_wildcard_rejected(self, auth_manager, test_user):
        from src.core.auth.exceptions import InvalidPermissionError

        with pytest.raises(InvalidPermissionError):
            auth_manager.update_user(test_user.id, permissions={"*": True})

    def test_unknown_key_rejected(self, auth_manager, test_user):
        from src.core.auth.exceptions import InvalidPermissionError

        with pytest.raises(InvalidPermissionError):
            auth_manager.update_user(
                test_user.id, permissions={"admin.everything": True}
            )

    def test_known_key_accepted(self, auth_manager, test_user):
        user = auth_manager.update_user(
            test_user.id, permissions={"messages.send": True}
        )
        assert user.permissions.get("messages.send") is True


class TestOAuthNonceValidation:
    """M6: the id_token nonce must be checked against the stored state."""

    @pytest.mark.asyncio
    async def test_nonce_mismatch_rejected(self):
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        wrong_jwt = _make_id_token({"nonce": "attacker-nonce"})
        token_response = MagicMock()
        token_response.raise_for_status = lambda: None
        token_response.json = lambda: {
            "access_token": "tok",
            "id_token": wrong_jwt,
        }
        client_mock = MagicMock()
        client_mock.post = AsyncMock(return_value=token_response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client_mock)
        context.__aexit__ = AsyncMock(return_value=None)

        state_record = SimpleNamespace(
            nonce_value="expected-nonce",
            cookie_issued=False,
            pkce_challenge=None,
        )
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))

        with (
            patch(
                "src.api.routes.auth.oauth.httpx.AsyncClient",
                return_value=context,
            ),
            patch(
                "src.api.routes.auth.oauth.api.get_auth",
                return_value=MagicMock(),
            ),
            patch(
                "src.api.routes.auth.oauth._redirect_uri_is_allowed",
                return_value=True,
            ),
            patch(
                "src.api.routes.auth.oauth.verify_oauth_state",
                return_value=(True, state_record, None),
            ),
            patch("src.api.routes.auth.oauth.config_util") as cu,
        ):
            cu.get.return_value = {
                "google": {"client_id": "id", "client_secret": "secret"}
            }
            callback_data = OAuthCallbackRequest(
                state="s", code="c", redirect_uri="http://localhost/cb"
            )
            with pytest.raises(HTTPException) as exc:
                await oauth_callback(request, MagicMock(), "google", callback_data)

        assert exc.value.status_code == 401
        assert "nonce" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_matching_nonce_passes_validation(self):
        """A matching nonce must NOT be rejected (reaches user-info fetch)."""
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        good_jwt = _make_id_token({"nonce": "expected-nonce"})
        token_response = MagicMock()
        token_response.raise_for_status = lambda: None
        token_response.json = lambda: {
            "access_token": "tok",
            "id_token": good_jwt,
        }
        client_mock = MagicMock()
        client_mock.post = AsyncMock(return_value=token_response)
        # user-info fetch follows; give it a 200 with minimal user data.
        user_info_response = MagicMock()
        user_info_response.raise_for_status = lambda: None
        user_info_response.json = lambda: {"email": "x@example.com"}
        client_mock.get = AsyncMock(return_value=user_info_response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client_mock)
        context.__aexit__ = AsyncMock(return_value=None)

        state_record = SimpleNamespace(
            nonce_value="expected-nonce",
            cookie_issued=False,
            pkce_challenge=None,
        )
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))

        with (
            patch(
                "src.api.routes.auth.oauth.httpx.AsyncClient",
                return_value=context,
            ),
            patch(
                "src.api.routes.auth.oauth.api.get_auth",
                return_value=MagicMock(),
            ),
            patch(
                "src.api.routes.auth.oauth._redirect_uri_is_allowed",
                return_value=True,
            ),
            patch(
                "src.api.routes.auth.oauth.verify_oauth_state",
                return_value=(True, state_record, None),
            ),
            patch("src.api.routes.auth.oauth.config_util") as cu,
            patch(
                "src.api.routes.auth.oauth._oauth_get_client_ip",
                return_value="1.2.3.4",
            ),
        ):
            cu.get.return_value = {
                "google": {"client_id": "id", "client_secret": "secret"}
            }
            callback_data = OAuthCallbackRequest(
                state="s", code="c", redirect_uri="http://localhost/cb"
            )
            try:
                await oauth_callback(request, MagicMock(), "google", callback_data)
            except HTTPException as exc:
                # Reaching user-info means nonce passed; a later linking
                # failure is fine, but a 401 nonce error is not.
                assert "nonce" not in str(exc.detail).lower()


class TestResetEmailLink:
    """L8: reset emails carry a deep link instead of a bare token."""

    def test_reset_email_contains_deep_link(self, auth_manager, test_user):
        from src.core.auth.manager import AuthManager

        manager = AuthManager.get_instance()
        sent = {}

        class FakeSender:
            def send(self, email, subject, body):
                sent["body"] = body
                sent["email"] = email
                return True

        manager.email_sender = FakeSender()

        result = auth_manager.request_password_reset(test_user.email)

        assert result is True
        assert "/reset-password?token=" in sent["body"]
        assert sent["email"] == test_user.email
