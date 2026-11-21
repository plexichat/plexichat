"""Regression: emoji mutation routes bind the emoji to the URL's server.

``get_emoji`` already verifies that the resolved emoji belongs to the server in
the URL. The PATCH/DELETE routes did not: they authorized against the URL's
``server_id`` but handed only ``emoji_id`` to the core. A caller who can manage
server A could therefore aim A's URL at an emoji owned by server B. The core
re-checks the owning server's permission, but the route still evicted A's cache
and broadcast A's ``GUILD_EMOJIS_UPDATE`` for a change that happened in B. These
tests pin the route boundary so mutation and read agree on the scope.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import emojis
from src.api.schemas.common import SnowflakeIDParam
from src.api.schemas.emojis import EmojiUpdateRequest


class TokenInfo:
    """Minimal stand-in; ``authenticated_cache_key`` reads these attributes."""

    def __init__(self, user_id):
        self.user_id = user_id
        self.token_type = "user"
        self.account_id = None


OWNING_SERVER = 999
URL_SERVER = 123
EMOJI_ID = 9


def _emoji(server_id):
    return SimpleNamespace(
        id=EMOJI_ID,
        server_id=server_id,
        name="hello",
        animated=False,
        url="",
        available=True,
        created_by=7,
        uploader_username="tester",
        created_at=1,
    )


class _FakeReactions:
    def __init__(self, owning_server):
        self.emoji = _emoji(owning_server)
        self.update_calls = []
        self.delete_calls = []

    def get_custom_emoji(self, eid):
        return self.emoji if eid == EMOJI_ID else None

    def update_custom_emoji(self, **kwargs):
        self.update_calls.append(kwargs)
        return self.emoji

    def delete_custom_emoji(self, user_id, eid):
        self.delete_calls.append((user_id, eid))
        return True

    def get_server_custom_emojis(self, sid):
        return [self.emoji] if sid == OWNING_SERVER else []


class _FakeServers:
    """Grants ``emojis.manage`` on every server (the route-scope permission)."""

    def get_permissions(self, user_id, server_id):
        return {"emojis.manage": True}


def _wire(monkeypatch, reactions):
    monkeypatch.setattr(emojis.api, "get_reactions", lambda: reactions)
    monkeypatch.setattr(emojis.api, "get_servers", lambda: _FakeServers())

    # Mutations fire a fire-and-forget dispatch task; keep the test focused on
    # the boundary decision, not on websocket fan-out.
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(emojis, "_dispatch_emoji_update", _noop)
    invalidations = []
    monkeypatch.setattr(
        emojis, "_invalidate_emoji_cache", lambda *a, **k: invalidations.append((a, k))
    )
    return invalidations


def _expect_404(coro):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 404


def test_update_emoji_rejects_emoji_from_another_server(monkeypatch):
    reactions = _FakeReactions(OWNING_SERVER)
    invalidations = _wire(monkeypatch, reactions)

    _expect_404(
        emojis.update_emoji(
            str(URL_SERVER),
            SnowflakeIDParam(EMOJI_ID),
            EmojiUpdateRequest(name="renamed"),
            TokenInfo(7),
        )
    )

    assert reactions.update_calls == []
    assert invalidations == []


def test_delete_emoji_rejects_emoji_from_another_server(monkeypatch):
    reactions = _FakeReactions(OWNING_SERVER)
    invalidations = _wire(monkeypatch, reactions)

    _expect_404(emojis.delete_emoji(str(URL_SERVER), str(EMOJI_ID), TokenInfo(7)))

    assert reactions.delete_calls == []
    assert invalidations == []


def test_update_emoji_allows_emoji_in_url_server(monkeypatch):
    reactions = _FakeReactions(URL_SERVER)
    invalidations = _wire(monkeypatch, reactions)

    result = asyncio.run(
        emojis.update_emoji(
            str(URL_SERVER),
            SnowflakeIDParam(EMOJI_ID),
            EmojiUpdateRequest(name="renamed"),
            TokenInfo(7),
        )
    )

    assert str(result.id) == str(EMOJI_ID)
    assert len(reactions.update_calls) == 1
    assert invalidations and invalidations[0][0] == (URL_SERVER, EMOJI_ID)


def test_delete_emoji_allows_emoji_in_url_server(monkeypatch):
    reactions = _FakeReactions(URL_SERVER)
    invalidations = _wire(monkeypatch, reactions)

    result = asyncio.run(
        emojis.delete_emoji(str(URL_SERVER), str(EMOJI_ID), TokenInfo(7))
    )

    assert result.success is True
    assert reactions.delete_calls == [(7, EMOJI_ID)]
    assert invalidations and invalidations[0][0] == (URL_SERVER, EMOJI_ID)
