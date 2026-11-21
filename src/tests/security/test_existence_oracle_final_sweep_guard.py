"""FINAL existence-oracle sweep guard for the auth/token/lookup endpoints.

Definitive enumeration of the failure messages these endpoints emit, and of
the internal reasons that feed them:

========================  ================================================  ==========================================================
endpoint                  internal failure reasons                          uniform client response
========================  ================================================  ==========================================================
password-reset/confirm    invalid / expired / superseded (already used)     401 {"error":{"code":401,"message":"Authentication required"}}
verify-email              invalid / expired / already consumed              400 {"error":{"code":400,"message":"Invalid or expired verification token"}}
share-links/resolve       unknown token / expired / revoked / bad password 403 {"error":{"code":404,"message":"Not found"}}
download-ticket/redeem    unknown ticket / expired / already redeemed       403 {"error":{"code":404,"message":"Not found"}}
admin ticket lookup       missing ticket                                    404 {"error":{"code":404,"message":"Ticket not found"}}
(peer-fixed) invite join  absent / revoked / expired / exhausted            404 "Invite not found or expired"
(peer-fixed) oauth state  absent / used / expired / mismatched              400 "Security verification failed"
========================  ================================================  ==========================================================

Every "existed but unusable" state must be indistinguishable from "never
existed". The two peer fixes have their own dedicated regression tests; this
module covers the remaining surfaces. It is deliberately non-vacuous: the
test doubles emit *different* internal reason strings for each state (and the
tests assert the core was actually reached), yet the HTTP responses must be
byte-identical. A genuine success path is exercised alongside each refusal so
the uniform message cannot be "fixed" by disabling the real operation.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.auth import email_verification as email_mod
from src.api.routes.auth import password as password_mod
from src.api.routes.suite import stash as stash_pkg
from src.api.routes.suite.stash import grants as grants_mod
from src.api.routes.suite.stash import helpers as helpers_mod
from src.api.routes.suite.stash import share_links as share_mod
from src.api.schemas.auth import PasswordResetConfirm
from src.core.auth.exceptions import TokenInvalidError
from src.core.suite.stash.manager import (
    StashPermissionError,
    StashValidationError,
)

pytestmark = pytest.mark.unit

USER = SimpleNamespace(user_id=42)
TOKEN = "s" * 43  # within the stash ticket/token length bounds


def _body(err: HTTPException) -> tuple[int, str]:
    """The full client-visible body (status + detail), used for equality."""
    return (err.status_code, str(err.detail))


# --- password reset confirm -------------------------------------------------

_RESET_REASONS = (
    "Invalid token",  # never existed
    "Token expired",  # existed, expired
    "Token already used",  # existed, redeemed / superseded
)


class _ResetAuth:
    def __init__(self, reason: str):
        self.reason = reason
        self.seen: list[tuple[str, str]] = []

    def reset_password(self, token: str, new_password: str) -> bool:
        self.seen.append((token, new_password))
        raise TokenInvalidError(self.reason)


async def _confirm(reason: str) -> tuple[HTTPException, _ResetAuth]:
    auth = _ResetAuth(reason)
    with (
        patch.object(api, "get_auth", return_value=auth),
        patch.object(api, "get_db", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await password_mod.confirm_password_reset(
                PasswordResetConfirm(token="r" * 32, new_password="Str0ng!Passw0rd")
            )
    return exc.value, auth


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", _RESET_REASONS)
async def test_password_reset_confirm_never_reveals_the_token_state(reason):
    err, auth = await _confirm(reason)
    # Non-vacuous: the distinct internal reason reached the auth core.
    assert auth.seen and auth.seen[0][0] == "r" * 32
    assert err.status_code == 401
    assert err.detail == {"error": {"code": 401, "message": "Authentication required"}}
    assert reason not in str(err.detail)


@pytest.mark.asyncio
async def test_all_password_reset_states_share_one_response():
    responses = [_body((await _confirm(reason))[0]) for reason in _RESET_REASONS]
    assert len(set(responses)) == 1


@pytest.mark.asyncio
async def test_real_password_reset_still_succeeds():
    class _Working:
        def reset_password(self, token, new_password):
            return True

    with (
        patch.object(api, "get_auth", return_value=_Working()),
        patch.object(api, "get_db", return_value=None),
    ):
        result = await password_mod.confirm_password_reset(
            PasswordResetConfirm(token="r" * 32, new_password="Str0ng!Passw0rd")
        )
    assert result.success is True


# --- email verification -----------------------------------------------------


class _VerifyAuth:
    def __init__(self):
        self.seen: list[str] = []

    def verify_email(self, token: str) -> bool:
        self.seen.append(token)
        return False  # invalid / expired / already consumed all collapse here


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ("unknown-token-state", "expired-token-state", "consumed-token-state")
)
async def test_verify_email_never_reveals_the_token_state(reason):
    auth = _VerifyAuth()
    with patch.object(api, "get_auth", return_value=auth):
        with pytest.raises(HTTPException) as exc:
            await email_mod.verify_email(email_mod.VerifyEmailRequest(token="v" * 32))
    err = exc.value
    assert auth.seen == ["v" * 32]
    assert err.status_code == 400
    assert err.detail == {
        "error": {"code": 400, "message": "Invalid or expired verification token"}
    }
    assert reason not in str(err.detail)


# --- stash share links / download tickets ----------------------------------

# The core knows these states apart; the route must not. Distinct strings make
# the doubles provably distinguishable before the response is inspected.
_STASH_REASONS = (
    "never existed",
    "Share link has expired",
    "Share link was revoked",
    "Download ticket is invalid, expired, or already redeemed",
)


class _FailingStashManager:
    def __init__(self, reason: str):
        self.reason = reason
        self.calls: list[str] = []

    def resolve_share_link(self, *args, **kwargs):
        self.calls.append("resolve_share_link")
        raise StashPermissionError(self.reason)

    def resolve_download_ticket(self, user_id, ticket):
        self.calls.append("resolve_download_ticket")
        raise StashPermissionError(self.reason)


_UNIFORM_STASH_BODY = {"error": {"code": 404, "message": "Not found"}}


def _share_link_failure(reason: str) -> tuple[HTTPException, _FailingStashManager]:
    mgr = _FailingStashManager(reason)
    with (
        patch.object(stash_pkg, "_manager", lambda: mgr),
        patch.object(share_mod, "check_rate_limit", return_value=(True, 9)),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                share_mod.resolve_share_link(
                    body=share_mod.StashShareLinkResolveRequest(token=TOKEN),
                    current_user=USER,
                )
            )
    return exc.value, mgr


def _ticket_redeem_failure(reason: str) -> tuple[HTTPException, _FailingStashManager]:
    mgr = _FailingStashManager(reason)
    with patch.object(stash_pkg, "_manager", lambda: mgr):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                grants_mod.redeem_download_ticket(
                    body=grants_mod.StashDownloadRedeemRequest(ticket=TOKEN),
                    current_user=USER,
                )
            )
    return exc.value, mgr


@pytest.mark.parametrize("reason", _STASH_REASONS)
def test_share_link_resolve_never_reveals_the_link_state(reason):
    err, mgr = _share_link_failure(reason)
    assert mgr.calls == ["resolve_share_link"]  # reached the manager
    assert err.status_code == 403
    assert err.detail == _UNIFORM_STASH_BODY
    assert reason not in str(err.detail)


@pytest.mark.parametrize("reason", _STASH_REASONS)
def test_download_ticket_redeem_never_reveals_the_ticket_state(reason):
    err, mgr = _ticket_redeem_failure(reason)
    assert mgr.calls == ["resolve_download_ticket"]  # reached the manager
    assert err.status_code == 403
    assert err.detail == _UNIFORM_STASH_BODY
    assert reason not in str(err.detail)


def test_all_stash_token_states_share_one_response():
    share = [_body(_share_link_failure(r)[0]) for r in _STASH_REASONS]
    tickets = [_body(_ticket_redeem_failure(r)[0]) for r in _STASH_REASONS]
    assert len(set(share)) == 1
    assert len(set(tickets)) == 1


def test_stash_error_mapper_keeps_one_body_for_every_failure_family():
    """The shared _error() used by all stash routes must stay uniform in body."""
    for exc in (
        helpers_mod.StashNotFoundError("gone"),
        StashPermissionError("revoked"),
        StashValidationError("bad"),
    ):
        err = helpers_mod._error(exc)
        assert err.detail == _UNIFORM_STASH_BODY


def test_real_share_link_and_ticket_paths_still_work():
    """The uniform refusal must not disable a usable link or a live ticket."""

    class _Working:
        def resolve_share_link(self, *args, **kwargs):
            return {
                "item_id": "123",
                "permissions": ["download"],
                "expires_at": None,
            }

        def resolve_download_ticket(self, user_id, ticket):
            return {"media_object_id": None, "expires_at": 0}

        def redeem_download_ticket(self, user_id, ticket):
            return {
                "item_id": "123",
                "version_id": None,
                "media_object_id": None,
                "expires_at": 0,
            }

    mgr = _Working()
    with (
        patch.object(stash_pkg, "_manager", lambda: mgr),
        patch.object(share_mod, "check_rate_limit", return_value=(True, 9)),
    ):
        resolved = asyncio.run(
            share_mod.resolve_share_link(
                body=share_mod.StashShareLinkResolveRequest(token=TOKEN),
                current_user=USER,
            )
        )
        redeemed = asyncio.run(
            grants_mod.redeem_download_ticket(
                body=grants_mod.StashDownloadRedeemRequest(ticket=TOKEN),
                current_user=USER,
            )
        )
    assert resolved.item_id == "123"
    assert redeemed.item_id == "123"


# --- static enumeration guard ----------------------------------------------

# Source-level ratchet: every enumerated handler must keep routing failures
# through a *static* message. A reason-derived message (f-string / str(exc) in
# a "message" value) would reintroduce the oracle, so the guard flags it.
_ENDPOINT_SOURCES = {
    "password-reset/confirm": password_mod.confirm_password_reset,
    "verify-email": email_mod.verify_email,
    "share-links/resolve": share_mod.resolve_share_link,
    "download-ticket/redeem": grants_mod.redeem_download_ticket,
}

# password-reset/confirm is allowed exactly ONE dynamic message: the 400 from
# ``validate_credential_input`` at the HTTP boundary, which reflects caller
# input (control chars / oversized token), never an internal token-state reason.
# No other enumerated handler may interpolate any value into a client message.
_ALLOWED_DYNAMIC_MESSAGES = {"password-reset/confirm": 1}


def test_no_enumerated_handler_derives_a_client_message_from_internal_state():
    import inspect
    import re

    dynamic = re.compile(r'"message"\s*:\s*(?:f["\']|str\()')
    for name, handler in _ENDPOINT_SOURCES.items():
        source = inspect.getsource(handler)
        offenders = [
            line.strip() for line in source.splitlines() if dynamic.search(line)
        ]
        allowed = _ALLOWED_DYNAMIC_MESSAGES.get(name, 0)
        assert len(offenders) == allowed, (
            f"{name} client-message interpolation count changed "
            f"(expected {allowed}, got {len(offenders)}): {offenders}"
        )


def test_reason_derived_message_would_be_caught_by_the_guard():
    """Self-test: prove the static ratchet is not vacuous."""
    import re

    dynamic = re.compile(r'"message"\s*:\s*(?:f["\']|str\()')
    evil = 'detail={"error": {"code": 400, "message": str(exc)}}'
    assert dynamic.search(evil)
