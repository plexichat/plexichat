"""Invite-code redemption must bound online guessing (credential stuffing).

An invite code is a bearer credential: whoever holds it can join the server.
Invite *creation* is rate limited (``_enforce_invite_quota``), but the redemption
endpoint ``POST /channels/invites/{code}`` had no dedicated guess budget at all --
only the generic transport limiter. A client with a stolen session could fire
thousands of candidate codes per minute, which is credential stuffing against
the whole code space.

The fix spends a shared, atomic budget on every guess BEFORE the core redeem
runs, keyed on the authoritative canonical client IP (and secondarily on the
user so several accounts from one host share the floor). A denial is a static
429 that is identical for a real-but-dead code and a never-existed one: the
endpoint must not become an existence oracle even when throttled.

These tests are the proof: against the unpatched route the unlimited-guessing
test sees every attempt reach ``use_invite`` and no 429.
"""

from __future__ import annotations

from typing import Any, Optional, cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.channels import invites as invites_route
from src.api.routes.channels.invites import ChannelInvitesMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.servers import exceptions as srv

pytestmark = [pytest.mark.unit]


def _user(user_id: int = 111) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _request(ip: Optional[str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/channels/invites/GUESS",
            "headers": [],
            "query_string": b"",
            "client": (ip, 12345) if ip else None,
        }
    )


class _CountingServers:
    """Core stub that records every redemption attempt."""

    def __init__(self, exc: BaseException):
        self._exc = exc
        self.attempts: list[str] = []

    def use_invite(self, user_id, code):
        self.attempts.append(code)
        raise self._exc


async def _join(fake, ip: Optional[str] = "203.0.113.7", code: str = "GUESSED1"):
    with (
        patch.object(api, "get_servers", return_value=fake),
        patch.object(invites_route, "get_client_ip", return_value=ip),
    ):
        return await ChannelInvitesMixin()._join_server_via_invite(
            code, _user(), request=_request(ip)
        )


def _deny(monkeypatch):
    monkeypatch.setattr(
        invites_route, "check_rate_limit", lambda *_a, **_k: (False, 0)
    )


def _allow(monkeypatch):
    monkeypatch.setattr(
        invites_route, "check_rate_limit", lambda *_a, **_k: (True, 9)
    )


def test_route_injects_request_for_authoritative_ip_keying():
    """FastAPI must inject the Request so the per-IP budget is live in prod."""
    from fastapi.dependencies.utils import get_dependant

    dependant = get_dependant(
        path="/api/v1/channels/invites/{invite_code}",
        call=ChannelInvitesMixin._join_server_via_invite,
    )
    # FastAPI records the special Request injection as request_param_name.
    assert dependant.request_param_name == "request"


def test_guess_key_is_authoritative_and_never_contains_the_code():
    """Key is keyed on canonical IP + user; the guessed code never leaks in it."""
    key = invites_route._invite_redeem_guess_key(111, "203.0.113.7")
    assert key.startswith("invites:redeem-guess:")
    # No secret is ever part of the key (there is no code argument at all).
    assert "SECRETCODE123" not in key
    # Canonical IPv4-mapped spelling folds to the same bucket as plain IPv4, so
    # rotating textual spellings of one host cannot multiply the budget.
    assert key == invites_route._invite_redeem_guess_key(111, "::ffff:203.0.113.7")
    # Different sources and different users get different buckets.
    assert key != invites_route._invite_redeem_guess_key(111, "203.0.113.8")
    assert key != invites_route._invite_redeem_guess_key(222, "203.0.113.7")


@pytest.mark.asyncio
async def test_exhausted_guess_budget_blocks_before_reaching_core(monkeypatch):
    """Pre-fix: check_rate_limit is never called and every guess hits core."""
    _deny(monkeypatch)
    fake = _CountingServers(srv.InviteNotFoundError("Invite not found"))
    with pytest.raises(HTTPException) as caught:
        await _join(fake)
    err = caught.value
    assert err.status_code == 429
    body = cast(dict[str, Any], err.detail)
    assert body["error"]["code"] == 429
    assert fake.attempts == [], "no redemption attempt may run once budget is spent"


@pytest.mark.asyncio
async def test_unlimited_guessing_is_bounded(monkeypatch):
    """The core guard: repeated wrong guesses are capped, not infinite."""
    import utils.config as config

    captured: dict[str, Any] = {}

    def counting(key, limit, window):
        captured.setdefault("calls", []).append((key, limit, window))
        n = len(captured["calls"])
        return (n <= limit, max(0, limit - n))

    monkeypatch.setattr(invites_route, "check_rate_limit", counting)
    monkeypatch.setattr(
        config, "get", lambda section, default=None: (
            {
                "redeem_guess_max": 3,
                "redeem_guess_window_seconds": 60,
                "redeem_guess_global_max": 0,
            }
            if section == "invites"
            else default
        ),
    )
    fake = _CountingServers(srv.InviteNotFoundError("Invite not found"))

    reached_core = 0
    throttled = 0
    for _ in range(10):
        try:
            await _join(fake)
        except HTTPException as exc:
            if exc.status_code == 429:
                throttled += 1
            else:
                # The guess was admitted and the core lookup ran (returns the
                # static 404 for a non-existent code). This is an allowed guess.
                assert exc.status_code == 404
                reached_core += 1

    # Pre-fix: 10/10 attempts reach core (the check_rate_limit call below is
    # never made), 0 throttled. Post-fix: the shared budget admits exactly
    # `redeem_guess_max` guesses and refuses the rest before any lookup.
    assert len(captured["calls"]) == 10, "every attempt must spend a shared slot"
    assert reached_core == 3
    assert throttled == 7
    assert len(fake.attempts) == 3
    assert all(c[1] == 3 and c[2] == 60 for c in captured["calls"])


@pytest.mark.asyncio
async def test_global_floor_is_active_by_default(monkeypatch):
    """A global floor is spent too, so rotating IPs cannot multiply the budget."""
    import utils.config as config

    calls = []

    def counting(key, limit, window):
        calls.append((key, limit, window))
        return True, 100

    monkeypatch.setattr(invites_route, "check_rate_limit", counting)
    # No [invites] overrides -> the module defaults apply.
    monkeypatch.setattr(config, "get", lambda section, default=None: default)
    fake = _CountingServers(srv.InviteNotFoundError("Invite not found"))
    with pytest.raises(HTTPException):
        await _join(fake)
    keys = [c[0] for c in calls]
    assert len(keys) == 2
    assert keys[0].startswith("invites:redeem-guess:ip:")
    assert keys[1] == "invites:redeem-guess:global"
    assert calls[1][1] == invites_route._REDEEM_GUESS_GLOBAL_MAX > 0


@pytest.mark.asyncio
async def test_throttled_refusal_is_uniform_for_all_code_states(monkeypatch):
    """A throttled answer must not distinguish real-dead from never-existed."""
    _deny(monkeypatch)
    bodies = []
    for exc in (
        srv.InviteNotFoundError("Invite not found"),
        srv.InviteExpiredError("Invite has expired", expired_at=1704153600000),
        srv.InviteMaxUsesError("Invite has reached maximum uses", max_uses=5, current_uses=5),
    ):
        fake = _CountingServers(exc)
        with pytest.raises(HTTPException) as caught:
            await _join(fake)
        bodies.append((caught.value.status_code, caught.value.detail))
    assert all(b == bodies[0] for b in bodies)
    assert bodies[0][1]["error"]["message"] == "Too many invite attempts"


@pytest.mark.asyncio
async def test_limiter_backend_fault_fails_closed(monkeypatch):
    """A limiter that cannot run must deny the guess, never allow it."""

    def boom(*_a, **_k):
        raise RuntimeError("limiter store down")

    monkeypatch.setattr(invites_route, "check_rate_limit", boom)
    fake = _CountingServers(srv.InviteNotFoundError("Invite not found"))
    with pytest.raises(HTTPException) as caught:
        await _join(fake)
    assert caught.value.status_code == 429
    assert fake.attempts == []


@pytest.mark.asyncio
async def test_allowed_guess_still_redeems(monkeypatch):
    """The budget must not disable real redemption (fails closed, not open)."""
    _allow(monkeypatch)

    class Working:
        def use_invite(self, user_id, code):
            return cast(Any, type("Member", (), {"server_id": 987654321})())

    with (
        patch.object(api, "get_servers", return_value=Working()),
        patch.object(invites_route, "get_client_ip", return_value="203.0.113.7"),
    ):
        result = await ChannelInvitesMixin()._join_server_via_invite(
            "LIVE1234", _user(), request=_request("203.0.113.7")
        )
    assert result.success is True
    assert str(result.server_id) == str(987654321)
