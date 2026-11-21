"""SEC700: channel message search re-checks access before a cached read.

``search_messages`` used to put ``@cached`` directly on the route handler. The
participant check lived inside that handler (and inside the core search), so a
viewer who lost access to the channel/conversation still received the warm
result body for the full cache TTL -- the decorator returns before the handler
body runs on a cache hit. The read now splits into a live gate in the route and
a data-only cached helper; these tests pin that ordering behaviourally.
"""

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.search as route
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CONV_ID = 123
VIEWER_ID = 42
WARM_BODY = ["warm-hit"]


def _viewer() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=VIEWER_ID,
        user_id=VIEWER_ID,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="viewer",
        account_type=AccountType.USER,
    )


class _Messaging:
    def __init__(self, *, participant=True, raises=False):
        self.participant = participant
        self.raises = raises
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        if self.raises:
            raise RuntimeError("participant store unavailable")
        return self.participant


def _prime_warm_cache(monkeypatch):
    """Simulate a warm cache entry; record whether the data path is entered."""
    entered = []

    async def _cached_helper(**kwargs):
        entered.append(kwargs)
        return WARM_BODY

    monkeypatch.setattr(route, "_search_messages_cached", _cached_helper)
    monkeypatch.setattr(api, "get_servers", lambda: None)
    return entered


@pytest.mark.asyncio
async def test_revoked_viewer_is_denied_before_warm_body(monkeypatch):
    entered = _prime_warm_cache(monkeypatch)
    messaging = _Messaging(participant=False)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CONV_ID), "needle", 25, _viewer())

    assert exc.value.status_code == 404
    # The live gate ran on this (would-be cache hit) request...
    assert messaging.checked == [(CONV_ID, VIEWER_ID)]
    # ...and the warm body was never served.
    assert entered == []


@pytest.mark.asyncio
async def test_participant_receives_warm_body(monkeypatch):
    entered = _prime_warm_cache(monkeypatch)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging(participant=True))

    assert (
        await route.search_messages(str(CONV_ID), "needle", 25, _viewer()) is WARM_BODY
    )
    assert entered and entered[0]["conv_id"] == CONV_ID
    assert entered[0]["content"] == "needle"
    assert entered[0]["current_user"].user_id == VIEWER_ID


@pytest.mark.asyncio
async def test_unavailable_participant_gate_fails_closed(monkeypatch):
    entered = _prime_warm_cache(monkeypatch)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging(raises=True))

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CONV_ID), "needle", 25, _viewer())

    assert exc.value.status_code == 404
    assert entered == []


@pytest.mark.asyncio
async def test_malformed_channel_id_denied_before_gate(monkeypatch):
    entered = _prime_warm_cache(monkeypatch)
    messaging = _Messaging(participant=True)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)

    with pytest.raises(HTTPException) as exc:
        await route.search_messages("1_2", "needle", 25, _viewer())

    assert exc.value.status_code == 400
    assert messaging.checked == []
    assert entered == []
