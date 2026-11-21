"""Regression tests for the server audit-log read path (GET /{server_id}/audit-logs).

The route enriches each audit entry with the acting user and the target
role/channel name. The user side was already batched through ``get_users_bulk``,
but role and channel names were resolved with one ``get_role`` / ``get_channel``
call per distinct target id (an N+1 fan-out that grew with the page size). These
tests pin the *cold* fan-out with counting fakes: enrichment must use a single
bulk role lookup and a single bulk channel lookup regardless of hit count, while
the resolved names and fail-closed fallbacks stay identical.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes.servers.audit_log import get_audit_log
from src.core.auth.models import AccountType

pytestmark = pytest.mark.unit


def _user(user_id: int = 42) -> TokenInfo:
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


class _CountingServers:
    """Records role/channel lookups; distinguishes bulk from per-id calls."""

    def __init__(self, count: int):
        self.entries = []
        for i in range(count):
            self.entries.append(
                SimpleNamespace(
                    id=1000 + i,
                    server_id=555,
                    user_id=7,
                    action_type=SimpleNamespace(value="update"),
                    target_type="role" if i % 2 == 0 else "channel",
                    target_id=str(8000 + i),
                    changes=None,
                    reason=None,
                    created_at=1_700_000_000_000,
                )
            )
        self.per_role_calls = []
        self.per_channel_calls = []
        self.bulk_role_calls = 0
        self.bulk_channel_calls = 0

    def get_audit_log(self, user_id, server_id, limit=50):
        return list(self.entries[:limit])

    # Per-id lookups (the old N+1 path) — fail loudly so a regression is obvious.
    def get_role(self, role_id, user_id):
        self.per_role_calls.append(role_id)
        return SimpleNamespace(name=f"role-{role_id}")

    def get_channel(self, channel_id, user_id):
        self.per_channel_calls.append(channel_id)
        return SimpleNamespace(name=f"channel-{channel_id}")

    # Bulk lookups (the expected batched path).
    def get_roles(self, user_id, server_id):
        self.bulk_role_calls += 1
        return [
            SimpleNamespace(id=e.target_id, name=f"role-{e.target_id}")
            for e in self.entries
            if e.target_type == "role"
        ]

    def get_channels(self, user_id, server_id):
        self.bulk_channel_calls += 1
        return [
            SimpleNamespace(id=e.target_id, name=f"channel-{e.target_id}")
            for e in self.entries
            if e.target_type == "channel"
        ]


class _Auth:
    def __init__(self):
        self.bulk_calls = []

    def get_users_bulk(self, user_ids):
        self.bulk_calls.append(list(user_ids))
        return {
            uid: SimpleNamespace(
                id=uid,
                username=f"user-{uid}",
                avatar_url=None,
                created_at=0,
                badges=[],
            )
            for uid in user_ids
        }


async def _run(count: int):
    servers = _CountingServers(count)
    auth = _Auth()
    with (
        patch.object(api, "get_servers", return_value=servers),
        patch.object(api, "get_auth", return_value=auth),
    ):
        result = await get_audit_log("555", limit=count, current_user=_user())
    return result, servers, auth


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 40])
async def test_role_channel_enrichment_is_batched_independent_of_hit_count(count):
    result, servers, auth = await _run(count)

    assert len(result) == count
    # One bulk role lookup and one bulk channel lookup, no matter the page size.
    assert servers.bulk_role_calls == 1, servers.bulk_role_calls
    assert servers.bulk_channel_calls == 1, servers.bulk_channel_calls
    assert servers.per_role_calls == [], servers.per_role_calls
    assert servers.per_channel_calls == [], servers.per_channel_calls
    # The user side stays batched too.
    assert len(auth.bulk_calls) == 1

    names = {int(e.target_id): e.target_name for e in result}
    for e in servers.entries:
        tid = int(e.target_id)
        expected = (
            f"role-{e.target_id}"
            if e.target_type == "role"
            else f"channel-{e.target_id}"
        )
        assert names[tid] == expected


@pytest.mark.asyncio
async def test_bulk_enrichment_fail_closed_when_permissions_denied():
    """A denied bulk role/channel lookup must not break the whole response."""
    servers = _CountingServers(4)

    def denied(user_id, server_id):
        raise HTTPException(status_code=403, detail="denied")

    servers.get_roles = denied
    servers.get_channels = denied
    with (
        patch.object(api, "get_servers", return_value=servers),
        patch.object(api, "get_auth", return_value=_Auth()),
    ):
        result = await get_audit_log("555", limit=4, current_user=_user())

    assert len(result) == 4
    assert all(e.target_name is None for e in result)
