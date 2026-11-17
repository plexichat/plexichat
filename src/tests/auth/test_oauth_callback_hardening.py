"""Regressions for OAuth callback trust-boundary hardening.

* The raw IdP token response / HTTP error body must never be interpolated into
  a log line — those payloads are attacker-influenced and can echo access,
  id and refresh tokens (CWE-532). Only structural metadata is logged.
* A PKCE ``code_verifier`` outside the RFC 7636 unreserved ASCII alphabet
  must be rejected at the route with a 400, not crash ``verify_pkce``'s
  ``str.encode("ascii")`` and leak a 500.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from src.api.routes.auth import oauth as oauth_mod
from src.api.schemas.auth import OAuthCallbackRequest

SECRET_ACCESS = "SECRET-access-marker-9f3a7c"
SECRET_ID = "SECRET-id-marker-9f3a7c"
SECRET_REFRESH = "SECRET-refresh-marker-9f3a7c"


def _state_record():
    return SimpleNamespace(
        nonce=None,
        nonce_value=None,
        cookie_issued=False,
        pkce_challenge=None,
    )


def _token_context(payload):
    token_response = MagicMock()
    token_response.raise_for_status = lambda: None
    token_response.json = lambda: payload
    client = MagicMock()
    client.post = AsyncMock(return_value=token_response)
    client.get = AsyncMock(return_value=MagicMock())
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx, client


def _config_patch():
    def get(key, default=None):
        if key == "oauth":
            return {"google": {"client_id": "id", "client_secret": "secret"}}
        return default

    return patch.object(oauth_mod.config_util, "get", side_effect=get)


def _base_patches(state_record, ctx):
    return (
        patch.object(oauth_mod.httpx, "AsyncClient", return_value=ctx),
        patch.object(oauth_mod.api, "get_auth", return_value=MagicMock()),
        patch.object(oauth_mod, "_redirect_uri_is_allowed", return_value=True),
        patch.object(
            oauth_mod, "verify_oauth_state", return_value=(True, state_record, None)
        ),
        patch.object(oauth_mod, "_oauth_get_client_ip", return_value="1.2.3.4"),
    )


def _logged_text(log_error):
    return " ".join(
        str(part)
        for call in log_error.call_args_list
        for part in (call.args, call.kwargs)
    )


@pytest.mark.auth
class TestOAuthCallbackLogRedaction:
    def test_safe_summary_helpers_emit_no_secret_values(self):
        summary = oauth_mod._safe_token_result_summary(
            {
                "error": "invalid_grant",
                "id_token": SECRET_ID,
                "refresh_token": SECRET_REFRESH,
            }
        )
        assert SECRET_ID not in summary
        assert SECRET_REFRESH not in summary
        assert "invalid_grant" in summary
        assert "keys=" in summary

        req = httpx.Request("POST", "https://oauth2.googleapis.com/token")
        resp = httpx.Response(
            400,
            json={"error": "invalid_grant", "access_token": SECRET_ACCESS},
            request=req,
        )
        err_summary = oauth_mod._safe_upstream_error_summary(resp)
        assert SECRET_ACCESS not in err_summary
        assert "status=400" in err_summary
        assert "invalid_grant" in err_summary

    @pytest.mark.asyncio
    async def test_missing_access_token_logs_no_raw_token_response(self):
        """A token response without access_token must not leak its other secrets."""
        from src.api.routes.auth.oauth import oauth_callback

        payload = {
            "error": "access_denied",
            "id_token": SECRET_ID,
            "refresh_token": SECRET_REFRESH,
        }
        ctx, _ = _token_context(payload)
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        patches = _base_patches(_state_record(), ctx)
        with patch.object(oauth_mod.logger, "error") as log_error, _config_patch():
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with pytest.raises(HTTPException) as exc:
                    await oauth_callback(
                        request,
                        MagicMock(),
                        "google",
                        OAuthCallbackRequest(
                            code="c", state="s", redirect_uri="http://x/cb"
                        ),
                    )
        assert exc.value.status_code == 401
        logged = _logged_text(log_error)
        assert SECRET_ID not in logged, logged
        assert SECRET_REFRESH not in logged, logged
        assert "Failed to get access token" in logged
        assert "keys=" in logged

    @pytest.mark.asyncio
    async def test_http_status_error_logs_no_raw_error_body(self):
        """An IdP 4xx/5xx body must not be logged verbatim (may echo tokens)."""
        from src.api.routes.auth.oauth import oauth_callback

        req = httpx.Request("POST", "https://oauth2.googleapis.com/token")
        resp = httpx.Response(
            400,
            json={"error": "invalid_grant", "access_token": SECRET_ACCESS},
            request=req,
        )
        err = httpx.HTTPStatusError("boom", request=req, response=resp)
        ctx, client = _token_context({"access_token": "unused"})
        client.post = AsyncMock(side_effect=err)
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        patches = _base_patches(_state_record(), ctx)
        with patch.object(oauth_mod.logger, "error") as log_error, _config_patch():
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with pytest.raises(HTTPException) as exc:
                    await oauth_callback(
                        request,
                        MagicMock(),
                        "google",
                        OAuthCallbackRequest(
                            code="c", state="s", redirect_uri="http://x/cb"
                        ),
                    )
        assert exc.value.status_code == 401
        logged = _logged_text(log_error)
        assert SECRET_ACCESS not in logged, logged
        assert "status=400" in logged, logged
        assert "invalid_grant" in logged, logged


@pytest.mark.auth
class TestOAuthVerifierValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "verifier",
        [
            "é" * 43,  # non-ASCII -> UnicodeEncodeError in verify_pkce
            "a" * 42 + "\n",  # control/whitespace char
            " " + "a" * 43,  # leading whitespace
        ],
    )
    async def test_malformed_verifier_rejected_with_400(self, verifier):
        from src.api.routes.auth.oauth import oauth_callback

        state = SimpleNamespace(
            nonce=None,
            nonce_value=None,
            cookie_issued=False,
            pkce_challenge="c" * 43,
        )
        ctx, _ = _token_context({"access_token": "tok"})
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        patches = _base_patches(state, ctx)
        with _config_patch():
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with pytest.raises(HTTPException) as exc:
                    await oauth_callback(
                        request,
                        MagicMock(),
                        "google",
                        OAuthCallbackRequest(
                            code="c",
                            state="s",
                            redirect_uri="http://x/cb",
                            code_verifier=verifier,
                        ),
                    )
        assert exc.value.status_code == 400
        assert "invalid format" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_valid_verifier_not_rejected_by_charset_gate(self):
        """A well-formed RFC 7636 verifier still passes the route boundary."""
        from src.api.routes.auth.oauth import oauth_callback

        state = SimpleNamespace(
            nonce=None,
            nonce_value=None,
            cookie_issued=False,
            pkce_challenge="c" * 43,
        )
        ctx, _ = _token_context({"access_token": "tok"})
        request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
        patches = _base_patches(state, ctx)
        with patch.object(oauth_mod, "verify_pkce", return_value=False) as verify:
            with _config_patch():
                with patches[0], patches[1], patches[2], patches[3], patches[4]:
                    with pytest.raises(HTTPException) as exc:
                        await oauth_callback(
                            request,
                            MagicMock(),
                            "google",
                            OAuthCallbackRequest(
                                code="c",
                                state="s",
                                redirect_uri="http://x/cb",
                                code_verifier="a" * 43,
                            ),
                        )
        # The well-formed verifier reaches PKCE verification (which we forced
        # to fail), so the error is the PKCE failure, not the 400 charset gate.
        assert verify.called
        assert exc.value.status_code == 400
        assert "PKCE" in str(exc.value.detail)
