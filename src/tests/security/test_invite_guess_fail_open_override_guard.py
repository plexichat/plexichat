"""Invite guessing must stay fail-closed under the global fail-open override.

The shared ``check_rate_limit`` honours an operator-wide
``rate_limiting.fail_open_on_backend_error`` escape hatch: on a limiter backend
fault it returns ``(True, limit)`` instead of denying. That is acceptable for
ordinary product limiters, but the invite-code redemption guess budget is a
bearer-credential guard whose contract is *unconditional* fail-closed. It must
not inherit the global override, or a single config line silently reopens
online brute force of invite codes with no code change to the invite route.

These tests first demonstrate the gap on the shared limiter (it admits a guess
on a fault when the override is on), then assert the invite-boundary limiter and
the join route deny the very same fault.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

import src.api as api
from src.api.routes.channels import invites as invites_route
from src.api.routes.channels.invites import ChannelInvitesMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache import rate_limit as rate_limit_mod
from src.core.servers import exceptions as srv

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _FaultyClient:
    """Valkey client that fails every limiter operation."""

    def eval_lua(self, *_a: Any, **_k: Any) -> Any:
        raise rate_limit_mod.ValkeyOperationError("limiter store down")


def _install_faulty_backend(
    monkeypatch: pytest.MonkeyPatch, *, fail_open: bool
) -> None:
    import utils.config as config

    monkeypatch.setattr(rate_limit_mod, "get_client", lambda: _FaultyClient())
    monkeypatch.setattr(rate_limit_mod, "is_available", lambda: True)
    monkeypatch.setattr(
        config,
        "get",
        lambda section, default=None: (
            {"fail_open_on_backend_error": fail_open}
            if section == "rate_limiting"
            else default
        ),
    )


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


def _request(ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/channels/invites/GUESS",
            "headers": [],
            "query_string": b"",
            "client": (ip, 12345),
        }
    )


class _CountingServers:
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def use_invite(self, user_id, code):
        self.attempts.append(code)
        raise srv.InviteNotFoundError("Invite not found")


def test_shared_limiter_honours_global_fail_open_config(monkeypatch) -> None:
    """Gap proof: with the override on, a backend fault grants a slot."""
    _install_faulty_backend(monkeypatch, fail_open=True)
    allowed, remaining = rate_limit_mod.check_rate_limit("probe:key", 5, 60)
    assert allowed is True, "shared limiter should fail open under this config"
    assert remaining == 5


def test_invite_guess_limiter_denies_fault_despite_fail_open_config(
    monkeypatch,
) -> None:
    """The invite-boundary limiter must deny the fault the shared one allowed."""
    _install_faulty_backend(monkeypatch, fail_open=True)
    allowed, remaining = invites_route.check_rate_limit(
        "invites:redeem-guess:ip:203.0.113.7:user:111", 5, 60
    )
    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_join_guess_fault_denies_before_core_with_fail_open_config(monkeypatch):
    """End to end: a faulty store + fail-open config cannot reach the core."""
    _install_faulty_backend(monkeypatch, fail_open=True)
    fake = _CountingServers()
    with (
        patch.object(api, "get_servers", return_value=fake),
        patch.object(invites_route, "get_client_ip", return_value="203.0.113.7"),
    ):
        with pytest.raises(HTTPException) as caught:
            await ChannelInvitesMixin()._join_server_via_invite(
                "GUESSED1", _user(), request=_request("203.0.113.7")
            )
    assert caught.value.status_code == 429
    assert fake.attempts == [], "no guess may reach use_invite on a limiter fault"
