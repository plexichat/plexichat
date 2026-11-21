"""Emoji read caches must re-check server membership before a warm hit.

``get_server_emojis`` and ``get_emoji`` used to carry ``@cached`` directly on
the route handler with the membership gate *inside* the cached body. On a cache
hit the decorator returns before that body runs, so a member who was kicked (or
otherwise lost access) kept receiving their still-warm emoji list/detail for the
whole TTL. Revocation also evicts these namespaces, but that is best-effort --
other revocation paths and stale process-local copies can miss it -- so the
per-request gate is the authoritative check and must run before the cache.

These tests warm the real in-memory cache, revoke membership behind its back,
and prove the route still denies (and never re-enters the data path). Run with::

    pytest src/tests/api/test_emoji_live_membership_gate_before_cache.py -q
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import emojis
from src.core.database.cache.base import mem_cache_matching_keys

pytestmark = [pytest.mark.api, pytest.mark.security]

UID = 7
SID = 123
EMOJI_ID = 9


class TokenInfo:
    """Minimal stand-in; ``authenticated_cache_key`` reads these attributes."""

    def __init__(self, user_id, token_type="user", account_id=None):
        self.user_id = user_id
        self.token_type = token_type
        self.account_id = account_id


def _fake_emoji(eid=EMOJI_ID):
    return SimpleNamespace(
        id=eid,
        server_id=SID,
        name="warm-emoji",
        animated=False,
        url="",
        available=True,
        created_by=1,
        uploader_username="u",
        created_at=1,
    )


class _FakeReactions:
    def __init__(self):
        self.list_calls = 0
        self.detail_calls = 0

    def get_server_custom_emojis(self, sid):
        self.list_calls += 1
        return [_fake_emoji()]

    def get_custom_emoji(self, eid):
        self.detail_calls += 1
        return _fake_emoji(eid)


class _FakeServers:
    def __init__(self, members=(), raises=False):
        self.members = set(members)
        self.raises = raises
        self.get_member_calls = []

    def get_member(self, sid, uid):
        self.get_member_calls.append((sid, uid))
        if self.raises:
            raise RuntimeError("member store unavailable")
        return object() if uid in self.members else None


def _wire(monkeypatch, reactions, servers):
    monkeypatch.setattr(emojis.api, "get_reactions", lambda: reactions)
    monkeypatch.setattr(emojis.api, "get_servers", lambda: servers)
    return TokenInfo(UID)


async def _expect_http(coro, status):
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == status


def _name(payload):
    """Cache hits reconstruct to dicts, cold reads return response models."""
    return payload.get("name") if isinstance(payload, dict) else payload.name


@pytest.mark.asyncio
async def test_revoked_member_denied_on_warm_list_cache(monkeypatch):
    """A kicked member cannot read a still-warm list; the live gate denies."""
    reactions = _FakeReactions()
    servers = _FakeServers({UID})
    token = _wire(monkeypatch, reactions, servers)

    # Warm the real cache while still a member.
    first = await emojis.get_server_emojis(server_id=str(SID), current_user=token)
    assert first and reactions.list_calls == 1
    assert servers.get_member_calls == [(SID, UID)]
    assert mem_cache_matching_keys(f"{emojis._EMOJI_LIST_CACHE_PREFIX}:sid:{SID}:*")

    # Membership is revoked behind the cache's back.
    servers.members.clear()

    # Non-vacuity: the data-only helper would still serve the warm entry with
    # no backend call, so a cache hit *alone* would leak the list.
    warm = await emojis._load_server_emojis_page(server_id=str(SID), current_user=token)
    assert warm and reactions.list_calls == 1

    await _expect_http(
        emojis.get_server_emojis(server_id=str(SID), current_user=token), 403
    )
    # The live gate ran on this (would-be cache hit) request ...
    assert servers.get_member_calls == [(SID, UID), (SID, UID)]
    # ... and the data path was never re-entered.
    assert reactions.list_calls == 1


@pytest.mark.asyncio
async def test_revoked_member_denied_on_warm_detail_cache(monkeypatch):
    """The single-emoji detail route likewise re-gates before a warm hit."""
    reactions = _FakeReactions()
    servers = _FakeServers({UID})
    token = _wire(monkeypatch, reactions, servers)

    first = await emojis.get_emoji(
        server_id=str(SID), emoji_id=str(EMOJI_ID), current_user=token
    )
    assert first and reactions.detail_calls == 1
    assert mem_cache_matching_keys(
        f"{emojis._EMOJI_DETAIL_CACHE_PREFIX}:sid:{SID}:eid:{EMOJI_ID}:*"
    )

    servers.members.clear()

    warm = await emojis._load_emoji_detail(
        server_id=str(SID), emoji_id=str(EMOJI_ID), current_user=token
    )
    assert warm and reactions.detail_calls == 1

    await _expect_http(
        emojis.get_emoji(
            server_id=str(SID), emoji_id=str(EMOJI_ID), current_user=token
        ),
        403,
    )
    assert servers.get_member_calls == [(SID, UID), (SID, UID)]
    assert reactions.detail_calls == 1


@pytest.mark.asyncio
async def test_still_member_receives_warm_body(monkeypatch):
    """Positive control: an unchanged member still gets the cached payload."""
    reactions = _FakeReactions()
    servers = _FakeServers({UID})
    token = _wire(monkeypatch, reactions, servers)

    first = await emojis.get_server_emojis(server_id=str(SID), current_user=token)
    second = await emojis.get_server_emojis(server_id=str(SID), current_user=token)

    assert _name(first[0]) == _name(second[0]) == "warm-emoji"
    assert reactions.list_calls == 1
    assert servers.get_member_calls == [(SID, UID), (SID, UID)]


@pytest.mark.asyncio
async def test_failed_membership_probe_fails_closed_on_warm_cache(monkeypatch):
    """If the membership store errors, the cached body is not served."""
    reactions = _FakeReactions()
    servers = _FakeServers({UID})
    token = _wire(monkeypatch, reactions, servers)

    await emojis.get_server_emojis(server_id=str(SID), current_user=token)
    assert mem_cache_matching_keys(f"{emojis._EMOJI_LIST_CACHE_PREFIX}:sid:{SID}:*")

    servers.raises = True
    await _expect_http(
        emojis.get_server_emojis(server_id=str(SID), current_user=token), 500
    )
    assert reactions.list_calls == 1
