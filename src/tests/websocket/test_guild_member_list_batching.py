"""Query-budget and privacy regressions for REQUEST_GUILD_MEMBERS.

The guild roster enrichment used to call ``auth.get_user`` and
``presence.get_presence`` once per member: a guild with N members paid ~2N
threadpool/DB round trips, and the raw presence read ignored the viewer's
block list and invisible-mode scrubbing.  The handler now resolves distinct
users with two set-based helpers, chunked at ``_BULK_LOOKUP_CHUNK_SIZE`` ids,
and uses the *visible* presence resolver keyed by the authenticated viewer.

These tests pin:

* **result parity** - member order, names, nicknames, avatars, roles and
  presence are unchanged from the per-item implementation;
* **query-count reduction** - no per-item call survives, and a normal roster
  costs one bulk call per helper instead of one call per member;
* **bounds** - at most 500 ids are bound per bulk call, with
  ``ceil(N / 500)`` calls for a large roster;
* **no cross-user leak / fail closed** - presence is fetched through the
  viewer-scoped visible resolver, a blocked target renders offline, and a
  failed bulk read degrades to offline/fallback names rather than leaking or
  issuing per-id queries.
"""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.handlers.guild import (
    GuildHandler,
    _BULK_LOOKUP_CHUNK_SIZE,
)

pytestmark = [pytest.mark.websocket, pytest.mark.performance, pytest.mark.security]

VIEWER = 42
GUILD = 7


class _User:
    def __init__(self, user_id):
        self.id = user_id
        self.username = f"user-{user_id}"
        self.avatar_url = f"/avatars/{user_id}"


class _BulkAuth:
    """Bulk-only auth double; a per-user lookup is a test failure."""

    def __init__(self, fail=False):
        self.bulk_calls = []
        self.fail = fail

    def get_users_bulk(self, user_ids):
        self.bulk_calls.append(tuple(user_ids))
        if self.fail:
            raise RuntimeError("auth bulk store unavailable")
        return {uid: _User(uid) for uid in user_ids}

    def get_user(self, user_id):
        raise AssertionError("per-item auth.get_user must not be called")


class _BulkPresence:
    """Visible-bulk-only presence double with viewer scoping recorded."""

    def __init__(self, offline_ids=(), fail=False):
        self.bulk_calls = []
        self.offline_ids = set(offline_ids)
        self.fail = fail

    def get_visible_presences_bulk(self, viewer_id, user_ids):
        self.bulk_calls.append((viewer_id, tuple(user_ids)))
        if self.fail:
            raise RuntimeError("presence bulk store unavailable")
        result = {}
        for uid in user_ids:
            status = "offline" if uid in self.offline_ids else "online"
            result[uid] = SimpleNamespace(status=SimpleNamespace(value=status))
        return result

    def get_presence(self, user_id):
        raise AssertionError("per-item presence.get_presence must not be called")


def _members(user_ids, roles=(10, 20)):
    return [
        SimpleNamespace(
            id=1000 + uid,
            user_id=uid,
            nickname=f"nick-{uid}",
            avatar_url=None,
            joined_at=1700000000000 + uid,
            roles=list(roles),
        )
        for uid in user_ids
    ]


def _servers(members):
    servers = MagicMock()
    servers.get_server.return_value = SimpleNamespace(id=GUILD)
    # One page holds the whole fixture; the follow-up cursor call returns empty.
    pages = iter([list(members), []])
    servers.get_members.side_effect = lambda *args: next(pages)
    return servers


def _connection():
    # Presence is a privileged payload; the handler now only resolves it when
    # the connection declared GUILD_PRESENCES. These parity tests exercise the
    # presence path, so the test viewer must carry that bit.
    from src.core.events.types import GatewayIntent

    return SimpleNamespace(
        is_authenticated=True,
        user_id=VIEWER,
        intents=int(GatewayIntent.GUILD_PRESENCES),
    )


async def _run(monkeypatch, members, auth, presence):
    from src.api.websocket.handlers import guild as guild_module

    dispatcher = MagicMock()
    dispatcher.dispatch_to_connection = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.api.websocket.get_dispatcher", lambda: dispatcher, raising=True
    )
    monkeypatch.setattr("src.api.get_auth", lambda: auth, raising=True)
    monkeypatch.setattr("src.api.get_presence", lambda: presence, raising=True)

    handler = GuildHandler(_servers(members))
    await handler.handle_request_guild_members(_connection(), {"guild_id": GUILD})

    payloads = [
        call.args[1].data for call in dispatcher.dispatch_to_connection.await_args_list
    ]
    flat = [m for payload in payloads for m in payload["members"]]
    return flat, payloads, guild_module


@pytest.mark.asyncio
async def test_guild_members_result_parity_and_no_per_item_calls(monkeypatch):
    ids = list(range(1, 41))
    auth, presence = _BulkAuth(), _BulkPresence(offline_ids={3, 17})

    flat, payloads, _ = await _run(monkeypatch, _members(ids), auth, presence)

    # Parity with the replaced per-item loop: order, names, roles and presence.
    assert [m["user_id"] for m in flat] == [str(uid) for uid in ids]
    assert [m["username"] for m in flat] == [f"user-{uid}" for uid in ids]
    assert [m["nickname"] for m in flat] == [f"nick-{uid}" for uid in ids]
    assert [m["avatar_url"] for m in flat] == [f"/avatars/{uid}" for uid in ids]
    assert [m["roles"] for m in flat] == [["10", "20"]] * len(ids)
    assert [m["joined_at"] for m in flat] == [1700000000000 + uid for uid in ids]
    assert [m["presence"]["status"] for m in flat] == [
        "offline" if uid in {3, 17} else "online" for uid in ids
    ]
    # Truthful chunking is unchanged.
    assert payloads[0]["chunk_count"] == 1

    # Query-count reduction: 1 bulk call per helper instead of 40 per-item
    # calls each. The doubles raise if any per-item path is reintroduced.
    assert auth.bulk_calls == [tuple(ids)]
    assert presence.bulk_calls == [(VIEWER, tuple(ids))]


@pytest.mark.asyncio
async def test_guild_members_bulk_calls_are_chunked_and_bounded(monkeypatch):
    assert _BULK_LOOKUP_CHUNK_SIZE == 500
    # More than two chunks plus a duplicate user id (temporary memberships can
    # produce one row per membership for the same user).
    ids = list(range(1, 1004))
    members = _members(ids) + [_members([77])[0]]
    auth, presence = _BulkAuth(), _BulkPresence()

    flat, _, _ = await _run(monkeypatch, members, auth, presence)

    expected_calls = math.ceil(len(ids) / _BULK_LOOKUP_CHUNK_SIZE)
    assert len(auth.bulk_calls) == expected_calls
    assert len(presence.bulk_calls) == expected_calls
    for chunk in auth.bulk_calls:
        assert 1 <= len(chunk) <= _BULK_LOOKUP_CHUNK_SIZE
    for viewer, chunk in presence.bulk_calls:
        assert viewer == VIEWER
        assert 1 <= len(chunk) <= _BULK_LOOKUP_CHUNK_SIZE

    # Deduplicated, order-preserving union of every chunk.
    assert [uid for chunk in auth.bulk_calls for uid in chunk] == ids
    assert [uid for _, chunk in presence.bulk_calls for uid in chunk] == ids
    # Member output keeps its original order, including the duplicated user.
    assert [m["user_id"] for m in flat] == [str(uid) for uid in ids] + ["77"]


@pytest.mark.asyncio
async def test_guild_members_presence_is_viewer_scoped_and_fails_closed(monkeypatch):
    ids = [5, 6, 7]
    # A blocked target must render offline via the viewer-scoped resolver; the
    # raw per-user presence API (which ignores blocks) is never called.
    auth, presence = _BulkAuth(), _BulkPresence(offline_ids={6})

    flat, _, _ = await _run(monkeypatch, _members(ids), auth, presence)

    assert presence.bulk_calls == [(VIEWER, tuple(ids))]
    assert [m["presence"]["status"] for m in flat] == ["online", "offline", "online"]

    # A failing bulk presence read degrades to offline for every member.
    failing = _BulkPresence(fail=True)
    flat, _, _ = await _run(monkeypatch, _members(ids), _BulkAuth(), failing)
    assert [m["presence"]["status"] for m in flat] == ["offline"] * len(ids)


@pytest.mark.asyncio
async def test_guild_members_auth_failure_uses_fallback_names(monkeypatch):
    ids = [8, 9]
    flat, _, _ = await _run(
        monkeypatch, _members(ids), _BulkAuth(fail=True), _BulkPresence()
    )
    assert [m["username"] for m in flat] == ["User 8", "User 9"]
    # Presence resolution is independent of the auth failure.
    assert [m["presence"]["status"] for m in flat] == ["online", "online"]
