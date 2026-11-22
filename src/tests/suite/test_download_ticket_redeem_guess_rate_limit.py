"""Download-ticket redemption must bound online guessing (credential stuffing).

A Stash download ticket is a bearer credential: whoever holds it can fetch the
item's bytes. ``POST /suite/stash/download-ticket/redeem`` verifies that opaque
token through ``resolve_download_ticket``. Unlike its sibling share-link routes
(``share_links.py``) and invite redemption (``channels/invites.py``), this route
had NO dedicated guess budget -- only the permissive generic transport limiter.
An authenticated caller could therefore fire an unbounded number of candidate
ticket codes at the validator, each reaching the core lookup + SHA-256 check.

The fix spends a shared, atomic budget on every redemption guess BEFORE any
ticket lookup, keyed on the authoritative canonical client IP (and secondarily
on the validated user id, so several accounts from one host share the floor).
The guessed ticket is deliberately NOT part of the key: including it would give
every candidate its own fresh budget (no protection) and write the secret into
the rate-limit store. A denial is a static 429 that never carries ticket-state
information: the limiter runs before any lookup, so a throttled answer is
identical whether the ticket was real-but-dead or never existed. Any limiter
fault is treated as an exhausted budget (fail closed, never an unlimited path).

These tests are the proof: against the unpatched route the unlimited-guessing
test sees every attempt reach ``resolve_download_ticket`` and no 429.
"""

from __future__ import annotations

from typing import Any, Optional, cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes.suite.stash import grants as grants_route
from src.api.routes.suite.stash.grants import redeem_download_ticket
from src.core.auth.models import AccountType, TokenInfo
from src.core.suite.stash._shared import StashPermissionError

pytestmark = [pytest.mark.unit]

# StashDownloadRedeemRequest requires a 32..256 char opaque ticket.
TICKET = "G" * 32


def _user(user_id: int = 222) -> TokenInfo:
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
            "path": "/api/v1/suite/stash/download-ticket/redeem",
            "headers": [],
            "query_string": b"",
            "client": (ip, 12345) if ip else None,
        }
    )


class _CountingStash:
    """Core stub that records every redemption guess that reaches it."""

    def __init__(self, exc: BaseException):
        self._exc = exc
        self.attempts: list[str] = []

    def resolve_download_ticket(self, user_id, ticket):
        self.attempts.append(ticket)
        raise self._exc

    def redeem_download_ticket(self, user_id, ticket):
        self.attempts.append(ticket)
        raise self._exc


async def _redeem(fake, ip: Optional[str] = "203.0.113.7", ticket: str = TICKET):
    with (
        patch.object(grants_route, "_manager", lambda: fake),
        patch.object(grants_route, "get_client_ip", return_value=ip, create=True),
    ):
        return await redeem_download_ticket(
            grants_route.StashDownloadRedeemRequest(ticket=ticket),
            _user(),
            request=_request(ip),
        )


def _deny(monkeypatch):
    monkeypatch.setattr(
        grants_route, "check_rate_limit", lambda *_a, **_k: (False, 0), raising=False
    )


def _allow(monkeypatch):
    monkeypatch.setattr(
        grants_route, "check_rate_limit", lambda *_a, **_k: (True, 9), raising=False
    )


def test_route_injects_request_for_authoritative_ip_keying():
    """FastAPI must inject the Request so the per-IP budget is live in prod."""
    from fastapi.dependencies.utils import get_dependant

    dependant = get_dependant(
        path="/api/v1/suite/stash/download-ticket/redeem",
        call=redeem_download_ticket,
    )
    assert dependant.request_param_name == "request"


def test_guess_key_is_authoritative_and_never_contains_the_ticket():
    """Key is keyed on canonical IP + user; the guessed ticket never leaks in it."""
    key = grants_route._download_ticket_redeem_guess_key(222, "203.0.113.7")
    assert key.startswith("stash:download-ticket-redeem-guess:")
    assert "SECRETTICKET123" not in key
    # IPv4-mapped spelling folds to the same bucket as plain IPv4, so rotating
    # textual spellings of one host cannot multiply the budget.
    assert key == grants_route._download_ticket_redeem_guess_key(
        222, "::ffff:203.0.113.7"
    )
    # Different sources and different users get different buckets.
    assert key != grants_route._download_ticket_redeem_guess_key(222, "203.0.113.8")
    assert key != grants_route._download_ticket_redeem_guess_key(333, "203.0.113.7")
    # A missing IP falls back to ONE shared bucket, never an unlimited path.
    assert grants_route._download_ticket_redeem_guess_key(222, None) == (
        grants_route._download_ticket_redeem_guess_key(222, None)
    )


def test_unknown_ip_folds_to_one_shared_bucket():
    """Unknown source addresses collapse to a single authoritative bucket."""
    a = grants_route._download_ticket_redeem_guess_key(222, None)
    b = grants_route._download_ticket_redeem_guess_key(222, "not-an-ip")
    assert a == b


@pytest.mark.asyncio
async def test_exhausted_guess_budget_blocks_before_reaching_core(monkeypatch):
    """A spent budget must refuse before the core ticket validator runs."""
    _deny(monkeypatch)
    fake = _CountingStash(StashPermissionError("bad ticket"))
    with pytest.raises(HTTPException) as caught:
        await _redeem(fake)
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

    monkeypatch.setattr(grants_route, "check_rate_limit", counting, raising=False)
    monkeypatch.setattr(
        config,
        "get",
        lambda section, default=None: (
            {
                "redeem_guess_max": 3,
                "redeem_guess_window_seconds": 60,
                "redeem_guess_global_max": 0,
            }
            if section == "stash"
            else default
        ),
    )
    fake = _CountingStash(StashPermissionError("bad ticket"))

    reached_core = 0
    throttled = 0
    for _ in range(10):
        try:
            await _redeem(fake)
        except HTTPException as exc:
            if exc.status_code == 429:
                throttled += 1
            else:
                # The guess was admitted and the core lookup ran (the stub
                # raises a 403 for a non-existent ticket). This is an allowed
                # guess.
                assert exc.status_code == 403
                reached_core += 1

    # Pre-fix: 10/10 attempts reach core (the check_rate_limit call is never
    # made), 0 throttled. Post-fix: the shared budget admits exactly
    # `redeem_guess_max` guesses and refuses the rest before any lookup.
    assert len(captured.get("calls", [])) == 10, "every attempt must spend a slot"
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

    monkeypatch.setattr(grants_route, "check_rate_limit", counting, raising=False)
    monkeypatch.setattr(config, "get", lambda section, default=None: default)
    fake = _CountingStash(StashPermissionError("bad ticket"))
    with pytest.raises(HTTPException):
        await _redeem(fake)
    keys = [c[0] for c in calls]
    assert len(keys) == 2
    assert keys[0].startswith("stash:download-ticket-redeem-guess:ip:")
    assert keys[1].startswith("stash:download-ticket-redeem-guess:global")
    assert calls[1][1] == grants_route._REDEEM_GUESS_GLOBAL_MAX > 0


@pytest.mark.asyncio
async def test_throttled_refusal_is_uniform_for_all_ticket_states(monkeypatch):
    """A throttled answer must not distinguish real-dead from never-existed."""
    _deny(monkeypatch)
    bodies = []
    for exc in (
        StashPermissionError("never existed"),
        StashPermissionError(
            "Download ticket is invalid, expired, or already redeemed"
        ),
        StashPermissionError("Share link was revoked"),
    ):
        fake = _CountingStash(exc)
        with pytest.raises(HTTPException) as caught:
            await _redeem(fake)
        bodies.append((caught.value.status_code, caught.value.detail))
    assert all(b == bodies[0] for b in bodies)
    assert bodies[0][1]["error"]["message"] == "Too many download ticket attempts"


@pytest.mark.asyncio
async def test_limiter_backend_fault_fails_closed(monkeypatch):
    """A limiter that cannot run must deny the guess, never allow it."""

    def boom(*_a, **_k):
        raise RuntimeError("limiter store down")

    monkeypatch.setattr(grants_route, "check_rate_limit", boom, raising=False)
    fake = _CountingStash(StashPermissionError("bad ticket"))
    with pytest.raises(HTTPException) as caught:
        await _redeem(fake)
    assert caught.value.status_code == 429
    assert fake.attempts == []


@pytest.mark.asyncio
async def test_allowed_guess_still_redeems(monkeypatch):
    """The budget must not disable real redemption (fails closed, not open)."""
    _allow(monkeypatch)

    class Working:
        def resolve_download_ticket(self, user_id, ticket):
            return {"media_object_id": None, "expires_at": 0}

        def redeem_download_ticket(self, user_id, ticket):
            return {
                "item_id": "123",
                "version_id": None,
                "media_object_id": None,
                "expires_at": 0,
            }

    with (
        patch.object(grants_route, "_manager", lambda: Working()),
        patch.object(
            grants_route, "get_client_ip", return_value="203.0.113.7", create=True
        ),
    ):
        result = await redeem_download_ticket(
            grants_route.StashDownloadRedeemRequest(ticket=TICKET),
            _user(),
            request=_request("203.0.113.7"),
        )
    assert str(result.item_id) == "123"
