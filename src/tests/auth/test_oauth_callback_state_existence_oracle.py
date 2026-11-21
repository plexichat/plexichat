"""The OAuth callback must not leak *why* a state token failed validation.

``verify_oauth_state`` intentionally returns distinct internal reasons for a
failed state: "Invalid state token" (the token never existed) versus
"State token already used" / "State token expired" / "provider mismatch" /
"redirect URI mismatch" (the token existed but is unusable for this request).
The public, unauthenticated callback used to interpolate that reason straight
into its 400 body, and its state-cookie gate did the same by announcing a
"state cookie mismatch" -- a branch only reachable when a real state record
exists. Together those are an existence oracle on state tokens: a caller with
a guessed or intercepted token could learn whether it had ever been issued
and what had happened to it.

The fix keeps every check intact (invalid states are still rejected and the
state cookie is still required) and still logs the detailed reason for
operators, but returns one static response to the client. These tests prove
the responses are indistinguishable across all failure reasons, so the oracle
cannot reappear without failing the suite.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.auth import oauth as oauth_mod
from src.api.schemas.auth import OAuthCallbackRequest

pytestmark = pytest.mark.auth

_STATIC_MESSAGE = "Security verification failed"

# Reasons verify_state() may return on failure. The first is "never existed";
# the rest all prove the token existed in some state. "Missing state token"
# and the atomic-consume failures are included so every echo path is covered.
_INTERNAL_REASONS = (
    "Invalid state token",
    "State token already used",
    "State token expired",
    "State token provider mismatch",
    "State token redirect URI mismatch",
    "OAuth state could not be atomically consumed",
    "Missing state token",
)


def _body() -> OAuthCallbackRequest:
    return OAuthCallbackRequest(code="c", state="s", redirect_uri="http://localhost/cb")


async def _state_failure_response(reason: str):
    """Drive the callback through the failed-verify branch for ``reason``."""
    request = SimpleNamespace(client=("1.2.3.4", 1))
    with (
        patch.object(oauth_mod.api, "get_auth", return_value=MagicMock()),
        patch.object(oauth_mod, "_redirect_uri_is_allowed", return_value=True),
        patch.object(
            oauth_mod, "verify_oauth_state", return_value=(False, None, reason)
        ),
        patch.object(oauth_mod.logger, "warning") as log_warning,
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth_mod.oauth_callback(request, MagicMock(), "google", _body())
    return exc.value, log_warning


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", _INTERNAL_REASONS)
async def test_every_state_failure_reason_returns_same_static_message(reason: str):
    exc, log_warning = await _state_failure_response(reason)

    assert exc.status_code == 400
    detail = exc.detail["error"]
    assert detail["code"] == 400
    assert detail["message"] == _STATIC_MESSAGE
    # The distinguishing reason must not reach the client response at all.
    assert reason not in str(exc.detail)
    # The check is not weakened: the real reason is still logged server-side.
    assert any(reason in str(call.args) for call in log_warning.call_args_list)


@pytest.mark.asyncio
async def test_missing_state_cookie_is_indistinguishable_from_unknown_state():
    """Reaching the cookie gate proves the state exists; do not say so."""
    state_record = SimpleNamespace(
        nonce=None,
        nonce_value=None,
        cookie_issued=True,
        pkce_challenge=None,
    )
    request = SimpleNamespace(cookies={}, client=("1.2.3.4", 1))
    with (
        patch.object(oauth_mod.api, "get_auth", return_value=MagicMock()),
        patch.object(oauth_mod, "_redirect_uri_is_allowed", return_value=True),
        patch.object(
            oauth_mod, "verify_oauth_state", return_value=(True, state_record, None)
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth_mod.oauth_callback(request, MagicMock(), "google", _body())

    cookie_failure = exc.value
    assert cookie_failure.status_code == 400
    assert cookie_failure.detail["error"]["message"] == _STATIC_MESSAGE
    assert "cookie" not in cookie_failure.detail["error"]["message"].lower()

    # Non-vacuous: the "never existed" branch returns the identical response,
    # so the caller cannot tell an existing-but-cookieless state from a bogus one.
    unknown_state, _ = await _state_failure_response("Invalid state token")
    assert unknown_state.status_code == cookie_failure.status_code
    assert unknown_state.detail == cookie_failure.detail
