"""
Regression tests for M6: OAuth nonce must be bound to state.

The raw nonce is never persisted — only its SHA-256 hash rides with the
state record. The callback re-hashes the id_token ``nonce`` claim and
compares in constant time, fails closed when a nonce-bound flow presents
no/mismatched nonce, and the state remains single-use (consumed by
verify_state before the nonce check, so failures leave nothing replayable).
"""

import base64
import hashlib
import json as _json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_id_token(claims):
    header = base64.urlsafe_b64encode(
        _json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(_json.dumps(claims).encode()).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.sig"


def _hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _bound_state(nonce_raw: str) -> SimpleNamespace:
    """Route-level state record as verify_state returns it (hash only)."""
    return SimpleNamespace(
        nonce=_hash(nonce_raw),
        nonce_value=None,
        cookie_issued=False,
        pkce_challenge=None,
    )


def _token_context(id_token=None, user_info=None):
    token_response = MagicMock()
    token_response.raise_for_status = lambda: None
    payload = {"access_token": "tok"}
    if id_token is not None:
        payload["id_token"] = id_token
    token_response.json = lambda: payload
    client_mock = MagicMock()
    client_mock.post = AsyncMock(return_value=token_response)
    user_info_response = MagicMock()
    user_info_response.raise_for_status = lambda: None
    user_info_response.json = lambda: user_info or {"email": "x@example.com"}
    client_mock.get = AsyncMock(return_value=user_info_response)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client_mock)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def _callback_patches(state_record, context):
    return (
        patch("src.api.routes.auth.oauth.httpx.AsyncClient", return_value=context),
        patch("src.api.routes.auth.oauth.api.get_auth", return_value=MagicMock()),
        patch("src.api.routes.auth.oauth._redirect_uri_is_allowed", return_value=True),
        patch(
            "src.api.routes.auth.oauth.verify_oauth_state",
            return_value=(True, state_record, None),
        ),
        patch("src.api.routes.auth.oauth._oauth_get_client_ip", return_value="1.2.3.4"),
    )


@pytest.mark.auth
class TestNonceBinding:
    def test_nonce_stored_as_hash_not_plaintext(self, db):
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )
        assert state.nonce_value
        assert state.nonce == _hash(state.nonce_value)
        assert state.nonce_value not in (state.nonce or "")

    def test_verify_nonce_match_and_mismatch(self, db):
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )
        assert manager.verify_nonce(state.nonce, state.nonce_value) is True
        assert manager.verify_nonce(state.nonce, "attacker-nonce") is False
        # Fail closed on missing presentation.
        assert manager.verify_nonce(state.nonce, None) is False
        assert manager.verify_nonce(state.nonce, "") is False
        # No nonce bound (non-OIDC) -> check skipped.
        assert manager.verify_nonce(None, None) is True

    def test_verify_nonce_uses_constant_time_compare(self, db):
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )
        with patch(
            "secrets.compare_digest", wraps=__import__("secrets").compare_digest
        ) as spy:
            assert manager.verify_nonce(state.nonce, state.nonce_value) is True
            assert spy.called

    def test_verify_oauth_nonce_module_helper_fail_closed(self):
        from src.core.auth.oauth import verify_oauth_nonce

        h = _hash("expected-nonce")
        assert verify_oauth_nonce(h, "expected-nonce") is True
        assert verify_oauth_nonce(h, "wrong-nonce") is False
        assert verify_oauth_nonce(h, None) is False
        assert verify_oauth_nonce(None, None) is True

    def test_state_single_use_consume(self, db):
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )
        ok, _, _ = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        assert ok is True
        ok2, _, err2 = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        assert ok2 is False
        assert "used" in (err2 or "").lower()

    @pytest.mark.asyncio
    async def test_callback_rejects_mismatched_nonce(self):
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        state_record = _bound_state("expected-nonce")
        context = _token_context(id_token=_make_id_token({"nonce": "attacker-nonce"}))
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        p1, p2, p3, p4, p5 = _callback_patches(state_record, context)
        with p1, p2, p3, p4, p5, patch("src.api.routes.auth.oauth.config_util") as cu:
            cu.get.return_value = {"google": {"client_id": "id", "client_secret": "s"}}
            with pytest.raises(HTTPException) as exc:
                await oauth_callback(
                    request,
                    MagicMock(),
                    "google",
                    OAuthCallbackRequest(
                        state="s", code="c", redirect_uri="http://x/cb"
                    ),
                )
        assert exc.value.status_code == 401
        assert "nonce" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_callback_rejects_missing_id_token_when_nonce_bound(self):
        """Fail closed: nonce-bound flow with no id_token must not proceed."""
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        state_record = _bound_state("expected-nonce")
        context = _token_context(id_token=None)
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        p1, p2, p3, p4, p5 = _callback_patches(state_record, context)
        with p1, p2, p3, p4, p5, patch("src.api.routes.auth.oauth.config_util") as cu:
            cu.get.return_value = {"google": {"client_id": "id", "client_secret": "s"}}
            with pytest.raises(HTTPException) as exc:
                await oauth_callback(
                    request,
                    MagicMock(),
                    "google",
                    OAuthCallbackRequest(
                        state="s", code="c", redirect_uri="http://x/cb"
                    ),
                )
        assert exc.value.status_code == 401
        assert "nonce" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_callback_rejects_missing_nonce_claim(self):
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        state_record = _bound_state("expected-nonce")
        context = _token_context(id_token=_make_id_token({"sub": "123"}))
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        p1, p2, p3, p4, p5 = _callback_patches(state_record, context)
        with p1, p2, p3, p4, p5, patch("src.api.routes.auth.oauth.config_util") as cu:
            cu.get.return_value = {"google": {"client_id": "id", "client_secret": "s"}}
            with pytest.raises(HTTPException) as exc:
                await oauth_callback(
                    request,
                    MagicMock(),
                    "google",
                    OAuthCallbackRequest(
                        state="s", code="c", redirect_uri="http://x/cb"
                    ),
                )
        assert exc.value.status_code == 401
        assert "nonce" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_callback_accepts_matching_nonce(self):
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        state_record = _bound_state("expected-nonce")
        context = _token_context(id_token=_make_id_token({"nonce": "expected-nonce"}))
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        p1, p2, p3, p4, p5 = _callback_patches(state_record, context)
        with p1, p2, p3, p4, p5, patch("src.api.routes.auth.oauth.config_util") as cu:
            cu.get.return_value = {"google": {"client_id": "id", "client_secret": "s"}}
            try:
                await oauth_callback(
                    request,
                    MagicMock(),
                    "google",
                    OAuthCallbackRequest(
                        state="s", code="c", redirect_uri="http://x/cb"
                    ),
                )
            except HTTPException as exc:
                # Matching nonce must pass the nonce gate; any later-stage
                # error must not be a nonce error.
                assert "nonce" not in str(exc.detail).lower()

    @pytest.mark.asyncio
    async def test_callback_skips_nonce_when_not_bound(self):
        """Non-OIDC providers (no stored nonce) proceed without id_token."""
        from src.api.routes.auth.oauth import oauth_callback
        from src.api.schemas.auth import OAuthCallbackRequest

        state_record = SimpleNamespace(
            nonce=None, nonce_value=None, cookie_issued=False, pkce_challenge=None
        )
        context = _token_context(id_token=None)
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        p1, p2, p3, p4, p5 = _callback_patches(state_record, context)
        with p1, p2, p3, p4, p5, patch("src.api.routes.auth.oauth.config_util") as cu:
            cu.get.return_value = {"google": {"client_id": "id", "client_secret": "s"}}
            try:
                await oauth_callback(
                    request,
                    MagicMock(),
                    "google",
                    OAuthCallbackRequest(
                        state="s", code="c", redirect_uri="http://x/cb"
                    ),
                )
            except HTTPException as exc:
                assert "nonce" not in str(exc.detail).lower()
