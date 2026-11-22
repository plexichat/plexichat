"""SECURITY (follow-up to N721): the *route-level* alias gate protects a warm
``@cached`` search-result page, so it too must fail CLOSED when the backing-
channel lookup is unproven. Mirrors the core-layer tests in
``test_message_search_alias_unproven_lookup_fails_closed.py``.

Run with: pytest src/tests/api/test_message_search_alias_unproven_lookup_fails_closed.py -q -n 0
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.search as route
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = 9601
CONV_ID = 9602
SERVER_ID = 9603
VIEWER_ID = 81


def _viewer():
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=VIEWER_ID,
        user_id=VIEWER_ID,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="read_denied_member",
        account_type=AccountType.USER,
    )


class _Messaging:
    def is_participant(self, conversation_id, user_id):
        return conversation_id == CONV_ID and user_id == VIEWER_ID


class _RaisingLookup:
    """Wired store; get_channel(alias) -> None, reverse lookup raises."""

    _setup_complete = True

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        return None  # alias is not a channel id

    def get_channel_by_conversation(self, conversation_id):
        raise RuntimeError("channel store unavailable")


class _MissingLookup:
    _setup_complete = True

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        return None


class _CorruptRow:
    _setup_complete = True

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        return None

    def get_channel_by_conversation(self, conversation_id):
        return {"id": None, "server_id": SERVER_ID}


class _DM:
    """Clean 'no backing channel' answer: a genuine DM/group."""

    _setup_complete = True

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        return None

    def get_channel_by_conversation(self, conversation_id):
        return None


class _Good:
    _setup_complete = True

    def __init__(self, messages_read):
        self.messages_read = messages_read
        self.permission_calls = []

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        if channel_id == CHANNEL_ID:
            return SimpleNamespace(
                id=CHANNEL_ID, server_id=SERVER_ID, conversation_id=CONV_ID
            )
        return None

    def get_channel_by_conversation(self, conversation_id):
        if conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": SERVER_ID}
        return None

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        return self.messages_read


def _prime_cached_helper(monkeypatch):
    entered = []

    async def _cached(**kwargs):
        entered.append(kwargs)
        return ["channel-message-hit"]

    monkeypatch.setattr(route, "_search_messages_cached", _cached)
    return entered


def _wire(monkeypatch, servers):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging())


@pytest.mark.asyncio
@pytest.mark.parametrize("servers", [_RaisingLookup(), _MissingLookup(), _CorruptRow()])
async def test_unproven_alias_lookup_denies_before_cache(monkeypatch, servers):
    entered = _prime_cached_helper(monkeypatch)
    _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CONV_ID), "secret", 25, _viewer())
    assert exc.value.status_code in (403, 404)
    assert entered == []  # the warm/cold cached body was never read


@pytest.mark.asyncio
async def test_genuine_dm_still_searched(monkeypatch):
    """Control: a clean 'no backing channel' answer is not over-denied."""
    entered = _prime_cached_helper(monkeypatch)
    _wire(monkeypatch, _DM())

    result = await route.search_messages(str(CONV_ID), "dm text", 25, _viewer())
    assert result == ["channel-message-hit"]
    assert entered and entered[0]["conv_id"] == CONV_ID


@pytest.mark.asyncio
async def test_valid_backing_honors_read_grant(monkeypatch):
    """Control: the legitimate alias path is still gated and still works."""
    ok = _Good(messages_read=True)
    _prime_cached_helper(monkeypatch)
    _wire(monkeypatch, ok)
    assert await route.search_messages(str(CONV_ID), "hello", 25, _viewer()) == [
        "channel-message-hit"
    ]
    assert (VIEWER_ID, SERVER_ID, "messages.read", CHANNEL_ID) in ok.permission_calls

    denied = _Good(messages_read=False)
    entered2 = _prime_cached_helper(monkeypatch)
    _wire(monkeypatch, denied)
    with pytest.raises(HTTPException):
        await route.search_messages(str(CONV_ID), "secret", 25, _viewer())
    assert entered2 == []
