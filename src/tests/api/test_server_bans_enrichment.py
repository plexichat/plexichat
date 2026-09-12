"""Ban-list enrichment regressions (Agent 39 F-5).

The client rendered ``ban.user?.username || 'Unknown User'`` while the
list endpoint returned only ``user_id`` + ``reason``, so every row showed
"Unknown User". The route now bulk-resolves the banned users' public
info into ``BanResponse.user``. These tests pin that contract, including
the graceful degradation paths (a deleted user, and a failed bulk lookup)
so the endpoint never 500s just because a username could not be resolved.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import src.api as api
from src.api.routes.servers import bans as bans_route


def _ban(user_id: int, reason: str = "spam") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id, reason=reason, banned_by=999, created_at=1_700_000_000
    )


def _user(user_id: int, username: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        username=username,
        avatar_url=None,
        created_at=1_600_000_000,
        badges=[],
    )


def _install(monkeypatch, *, bans, users, get_users_bulk=None):
    servers_mod = SimpleNamespace(get_bans=lambda actor, sid: bans)
    auth_mod = SimpleNamespace(
        get_users_bulk=get_users_bulk
        or (lambda ids: {uid: users[uid] for uid in ids if uid in users})
    )
    monkeypatch.setattr(api, "get_servers", lambda: servers_mod)
    monkeypatch.setattr(api, "get_auth", lambda: auth_mod)


def test_ban_rows_are_enriched_with_public_user(monkeypatch):
    _install(
        monkeypatch,
        bans=[_ban(101), _ban(202, reason="raid")],
        users={101: _user(101, "alice"), 202: _user(202, "bob")},
    )
    current_user = SimpleNamespace(user_id=1)

    result = asyncio.run(bans_route.get_server_bans("7", current_user))

    assert [r.user_id for r in result] == ["101", "202"]
    assert result[0].user is not None and result[0].user.username == "alice"
    assert result[1].user is not None and result[1].user.username == "bob"
    # Enrichment must not disturb the ban metadata itself.
    assert result[1].reason == "raid"
    assert result[1].banned_at == 1_700_000_000


def test_unknown_banned_user_degrades_to_null_without_500(monkeypatch):
    # User 202 was hard-deleted: the bulk lookup simply has no entry.
    _install(
        monkeypatch,
        bans=[_ban(101), _ban(202)],
        users={101: _user(101, "alice")},
    )
    current_user = SimpleNamespace(user_id=1)

    result = asyncio.run(bans_route.get_server_bans("7", current_user))

    assert result[0].user is not None and result[0].user.username == "alice"
    assert result[1].user is None, "missing users must stay None, not raise"


def test_bulk_lookup_failure_does_not_break_the_list(monkeypatch):
    def _boom(_ids):
        raise RuntimeError("auth backend unavailable")

    _install(
        monkeypatch,
        bans=[_ban(101)],
        users={101: _user(101, "alice")},
        get_users_bulk=_boom,
    )
    current_user = SimpleNamespace(user_id=1)

    result = asyncio.run(bans_route.get_server_bans("7", current_user))

    assert len(result) == 1
    assert result[0].user_id == "101"
    assert result[0].user is None, "failed enrichment must degrade, not 500"


def test_invalid_server_id_is_a_400(monkeypatch):
    _install(monkeypatch, bans=[], users={})
    from fastapi import HTTPException

    try:
        asyncio.run(bans_route.get_server_bans("not-an-id", SimpleNamespace(user_id=1)))
    except HTTPException as exc:
        assert exc.status_code == 400
    else:  # pragma: no cover - guard against silent acceptance
        raise AssertionError("invalid server id must raise HTTPException(400)")
