"""REQUEST_GUILD_MEMBERS must not leak presence without GUILD_PRESENCES.

``GUILD_MEMBERS_CHUNK`` is authorized at dispatch time by ``GUILD_MEMBERS``
only (``EVENT_INTENT_MAP``).  The chunk body, however, also carries each
member's real presence status -- a *separate* privileged payload.  A
connection holding GUILD_MEMBERS but not GUILD_PRESENCES must therefore never
see a non-offline status, and must not even trigger the presence bulk read.

These tests are deliberately non-vacuous: the positive control proves the
roster (identities/roles) and the presence path both still work when the
intent is declared, while the negative cases prove the gate suppresses only
the privileged presence data.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.handlers.guild import GuildHandler
from src.core.events.types import GatewayIntent

pytestmark = [pytest.mark.websocket, pytest.mark.security]

VIEWER = 42
GUILD = 7


class _User:
    def __init__(self, user_id):
        self.id = user_id
        self.username = f"user-{user_id}"
        self.avatar_url = None


class _BulkAuth:
    def get_users_bulk(self, user_ids):
        return {uid: _User(uid) for uid in user_ids}


class _BulkPresence:
    """Records whether the privileged presence read was attempted at all."""

    def __init__(self):
        self.calls = []

    def get_visible_presences_bulk(self, viewer_id, user_ids):
        self.calls.append((viewer_id, tuple(user_ids)))
        online = SimpleNamespace(status=SimpleNamespace(value="online"))
        return {uid: online for uid in user_ids}


def _members(user_ids):
    return [
        SimpleNamespace(
            id=1000 + uid,
            user_id=uid,
            nickname=None,
            avatar_url=None,
            joined_at=1,
            roles=[10, 20],
        )
        for uid in user_ids
    ]


async def _run(monkeypatch, intents, members=(1, 2, 3)):
    servers = MagicMock()
    servers.get_server.return_value = SimpleNamespace(id=GUILD)
    pages = iter([list(_members(members)), []])
    servers.get_members.side_effect = lambda *args: next(pages)

    presence = _BulkPresence()
    dispatcher = MagicMock()
    dispatcher.dispatch_to_connection = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "src.api.websocket.get_dispatcher", lambda: dispatcher, raising=True
    )
    monkeypatch.setattr("src.api.get_auth", lambda: _BulkAuth(), raising=True)
    monkeypatch.setattr("src.api.get_presence", lambda: presence, raising=True)

    conn = SimpleNamespace(is_authenticated=True, user_id=VIEWER, intents=intents)
    await GuildHandler(servers).handle_request_guild_members(conn, {"guild_id": GUILD})

    payloads = [
        call.args[1].data for call in dispatcher.dispatch_to_connection.await_args_list
    ]
    flat = [m for payload in payloads for m in payload.get("members", [])]
    return flat, presence.calls


@pytest.mark.asyncio
async def test_presence_requires_guild_presences_intent(monkeypatch):
    """GUILD_MEMBERS alone authorizes the roster, never the presence payload."""
    mask = int(GatewayIntent.GUILD_MEMBERS)
    flat, presence_calls = await _run(monkeypatch, mask)

    # Non-vacuous: the roster itself was still resolved and delivered.
    assert [m["user_id"] for m in flat] == ["1", "2", "3"]
    assert [m["roles"] for m in flat] == [["10", "20"]] * 3
    # The privileged payload is withheld, and not even read from the store.
    assert [m["presence"]["status"] for m in flat] == ["offline"] * 3
    assert presence_calls == []


@pytest.mark.asyncio
async def test_presence_delivered_when_intent_declared(monkeypatch):
    """Positive control: declaring both intents restores real presence."""
    mask = int(GatewayIntent.GUILD_MEMBERS | GatewayIntent.GUILD_PRESENCES)
    flat, presence_calls = await _run(monkeypatch, mask)

    assert [m["presence"]["status"] for m in flat] == ["online"] * 3
    assert presence_calls == [(VIEWER, (1, 2, 3))]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_mask", [0, None, -1, True, "garbage", 2**40])
async def test_presence_fails_closed_on_invalid_or_missing_mask(monkeypatch, bad_mask):
    """No/absent/corrupt intent masks never grant the privileged read."""
    flat, presence_calls = await _run(monkeypatch, bad_mask)

    assert [m["user_id"] for m in flat] == ["1", "2", "3"]
    assert [m["presence"]["status"] for m in flat] == ["offline"] * 3
    assert presence_calls == []
