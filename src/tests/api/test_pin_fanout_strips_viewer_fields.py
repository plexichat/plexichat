"""Pin/unpin MESSAGE_UPDATE fan-out must not carry the pinner's viewer fields.

``pin_message`` / ``unpin_message`` build a single viewer-scoped
``MessageResponse`` (``viewer_user_id=pinner``) and broadcast its serialization
to every channel viewer. For a poll-bearing message that body carries the
pinner's poll results (per-option counts, voter headcount and their own votes,
resolved through ``get_results(viewer)``), read state and status -- all bound
to that one viewer's window. Every recipient of the pin/unpin MESSAGE_UPDATE
received them. The fix routes the fan-out copy through
``_fanout_safe_message_body``; the caller's HTTP result is a bare
``SuccessResponse``, so nothing viewer-specific is lost to the actor.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api
import src.api.websocket as ws_module
from src.api.routes.messages import pins as pins_mod
from src.api.routes.messages.helpers import _message_to_response
from src.core.auth.models import AccountType, TokenInfo
from src.core.polls.models import Poll, PollOption, PollResults, PollResultsVisibility

pytestmark = [pytest.mark.asyncio, pytest.mark.api]

POLL_ID = 900
MSG_ID = 1000
CID = 55
PINNER = 7


class RecordingDispatcher:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


def _poll() -> Poll:
    return Poll(
        id=POLL_ID,
        message_id=MSG_ID,
        question="Secret poll",
        created_by=PINNER,
        created_at=100,
        results_visibility=PollResultsVisibility.AFTER_VOTE,
        options=[
            PollOption(id=10, poll_id=POLL_ID, text="a", position=0),
            PollOption(id=11, poll_id=POLL_ID, text="b", position=1),
        ],
        total_votes=3,
        is_ended=False,
    )


class _AuthorResultsPolls:
    """get_results() as the entitled author/pinner sees it (counts + votes)."""

    def get_results(self, poll_id, user_id):
        return PollResults(
            poll=_poll(),
            options=[
                PollOption(id=10, poll_id=POLL_ID, text="a", position=0, vote_count=2),
                PollOption(id=11, poll_id=POLL_ID, text="b", position=1, vote_count=1),
            ],
            total_votes=3,
            user_voted=True,
            user_votes=[10],
        )


class _FakeMessaging:
    def __init__(self, msg):
        self._msg = msg

    def get_message(self, user_id, mid):
        return self._msg

    def pin_message(self, user_id, mid):
        return True

    def unpin_message(self, user_id, mid):
        return True


def _token_info(user_id):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="pinner",
        account_type=AccountType.USER,
    )


async def _drain_broadcast_tasks():
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def env(monkeypatch):
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: dispatcher)

    # An object (not a dict): the route's channel-guard reads conversation_id
    # with getattr, while _message_to_response's _get_attr handles both shapes.
    msg = SimpleNamespace(
        id=MSG_ID,
        author_id=PINNER,
        content="secret vote",
        created_at=1_700_000_000_000,
        channel_id=CID,
        conversation_id=CID,
        metadata={"poll_id": POLL_ID},
        embeds=[],
        # Viewer-bound fields hydrated for the pinner by _message_to_response.
        read=True,
        status="read",
    )
    monkeypatch.setattr(api, "get_messaging", lambda: _FakeMessaging(msg))
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_polls", lambda: _AuthorResultsPolls())
    monkeypatch.setattr(api, "get_embeds", lambda: None, raising=False)

    # Recipient resolution and the separate pins-updated event are not what
    # this test targets; pin the audience and stub the secondary emit.
    monkeypatch.setattr(
        pins_mod, "_resolve_recipients", lambda *a, **k: ([11, 22], None)
    )
    import src.core.events.gateway_emit as gateway_emit

    monkeypatch.setattr(gateway_emit, "emit_channel_pins_update", lambda **k: None)

    return dispatcher, msg


def _assert_nonvacuous(msg):
    """The actor's own serialization really carries each private field."""
    raw = _message_to_response(
        msg, viewer_user_id=PINNER, suppress_embeds=True
    ).model_dump()
    assert raw["poll_results"] is not None
    assert raw["poll_results"]["total_votes"] == 3
    assert [o["vote_count"] for o in raw["poll_results"]["options"]] == [2, 1]
    assert raw["poll"]["total_votes"] == 3
    assert raw["read"] is True
    assert raw["status"] == "read"


def _assert_clean_on_wire(dispatcher):
    assert len(dispatcher.dispatched) == 1
    event, recipients = dispatcher.dispatched[0]
    assert set(recipients) == {11, 22}
    data = event.data

    # Viewer-specific poll data, read state and status are gone ...
    assert data["poll_results"] is None
    assert data["poll"]["total_votes"] is None
    assert all(o["vote_count"] is None for o in data["poll"]["options"])
    assert data["read"] is False
    assert data["read_by"] == []
    assert data["status"] is None
    # ... but the non-sensitive skeleton survives for the client merge.
    assert data["poll"]["question"] == "Secret poll"
    assert [o["id"] for o in data["poll"]["options"]] == ["10", "11"]
    assert data["content"] == "secret vote"


async def test_pin_broadcast_strips_viewer_fields(env):
    dispatcher, msg = env
    _assert_nonvacuous(msg)

    response = await pins_mod.pin_message(
        str(CID), str(MSG_ID), current_user=_token_info(PINNER)
    )
    assert response.success is True
    await _drain_broadcast_tasks()

    _assert_clean_on_wire(dispatcher)


async def test_unpin_broadcast_strips_viewer_fields(env):
    dispatcher, msg = env
    _assert_nonvacuous(msg)

    response = await pins_mod.unpin_message(
        str(CID), str(MSG_ID), current_user=_token_info(PINNER)
    )
    assert response.success is True
    await _drain_broadcast_tasks()

    _assert_clean_on_wire(dispatcher)
