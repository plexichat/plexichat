"""GitHub OAuth login must bind the authorization code with PKCE (S256).

GitHub's OAuth token endpoint supports RFC 7636 PKCE and accepts only the
S256 method. The provider table previously marked GitHub as *not* supporting
PKCE, so the login route issued challenge-less states and the callback never
verified a ``code_verifier``. An attacker who captures a victim's GitHub
authorization code (Referer leakage from the callback page, proxy logs, a
shared device) can then redeem it in their own browser using their own valid
state + state cookie, minting a session for the victim. PKCE binds each code
to the verifier only the initiating client holds, so GitHub rejects the
exchange when the wrong verifier is presented.

These tests exercise the real init route: before the fix, GitHub login with
no ``code_challenge`` succeeds (no PKCE), which is the failure they pin down.
"""

import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.auth import oauth as oauth_mod

REDIRECT = "https://app.example.com/oauth/callback"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest()).decode().rstrip("=")
)


def _oauth_config():
    return {
        "github": {
            "client_id": "cid",
            "client_secret": "secret",
            "allowed_redirect_uris": [REDIRECT],
        },
        "pkce_enabled": True,
    }


def _state():
    return SimpleNamespace(state_token="raw-state-token", nonce_value=None, nonce=None)


def _patches():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: (
        _oauth_config() if key == "oauth" else default
    )
    return (
        patch.object(oauth_mod, "config_util", cfg),
        patch.object(oauth_mod, "create_oauth_state", return_value=_state()),
        patch.object(oauth_mod, "_oauth_get_client_ip", return_value="1.2.3.4"),
    )


@pytest.mark.auth
@pytest.mark.asyncio
async def test_github_login_rejects_missing_code_challenge():
    """PKCE must be mandatory for GitHub, not silently optional."""
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await oauth_mod.oauth_login_init(
                MagicMock(), MagicMock(), "github", REDIRECT
            )
    assert exc.value.status_code == 400
    assert "code_challenge" in str(exc.value.detail)


@pytest.mark.auth
@pytest.mark.asyncio
async def test_github_login_binds_s256_challenge_to_state_and_auth_url():
    """A GitHub flow stores the S256 challenge and forwards it to the IdP."""
    p1, p2, p3 = _patches()
    with p1, p2 as create_state, p3:
        resp = await oauth_mod.oauth_login_init(
            MagicMock(),
            MagicMock(),
            "github",
            REDIRECT,
            code_challenge=CHALLENGE,
            code_challenge_method="S256",
        )
    assert create_state.call_args.kwargs["pkce_challenge"] == CHALLENGE
    assert f"code_challenge={CHALLENGE}" in resp.auth_url
    assert "code_challenge_method=S256" in resp.auth_url
