"""Message fan-out broadcasts must not carry one viewer's private fields.

``_broadcast_message_create`` / ``_broadcast_message_update`` serialize a
single ``MessageResponse`` and dump that one body to *every* channel
recipient. The producers (send / edit / pin) build that response with a
concrete viewer — the sender, editor or pinner — so it can carry that
viewer's poll results (resolved through ``get_results(viewer)``), read state
and reaction ``me`` flags. For a poll-bearing message the actor is normally
the author, who is entitled to per-option counts, the voter headcount and
their own votes; broadcasting the author's body hands all of that to members
who are not entitled to see it.

The sibling tombstone fix (``helpers._message_to_response``) only covers the
``viewer_user_id is None`` path. These real-viewer fan-out paths were still
leaking. The fix sanitizes the on-wire copy in the broadcast helper; the
actor's HTTP response keeps its full viewer-specific data.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api
import src.api.websocket as ws_module
from src.api.routes.messages.helpers import _message_to_response
from src.api.routes.messages.messages_crud import broadcast as broadcast_mod
from src.core.polls.models import Poll, PollOption, PollResults, PollResultsVisibility

pytestmark = [pytest.mark.messaging, pytest.mark.unit, pytest.mark.asyncio]

POLL_ID = 900
MSG_ID = 1000
AUTHOR = 7


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
        created_by=AUTHOR,
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
    """get_results() as the entitled author sees it (counts + own votes)."""

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


def _actor_response(monkeypatch):
    """The response a send/edit/pin producer builds for the actor/viewer."""
    monkeypatch.setattr(api, "get_polls", lambda: _AuthorResultsPolls())
    msg = {
        "id": MSG_ID,
        "author_id": AUTHOR,
        "content": "vote!",
        "created_at": 1_700_000_000_000,
        "channel_id": 55,
        "metadata": {"poll_id": POLL_ID},
        "embeds": [],
        # Per-viewer read state as hydrated by a list/get for this viewer.
        "read": True,
        "status": "read",
    }
    return _message_to_response(
        msg,
        viewer_user_id=AUTHOR,
        suppress_embeds=True,
        reactions_data=[
            {
                "emoji": "👍",
                "count": 5,
                "me": True,
                "url": None,
                "is_custom": False,
                "custom_emoji_id": None,
            }
        ],
    )


@pytest.fixture
def recording_ws(monkeypatch):
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: dispatcher)
    # Isolate this test from recipient resolution; it targets body sanitizing.
    monkeypatch.setattr(
        broadcast_mod, "_resolve_recipients", lambda *a, **k: ([11, 22], None)
    )
    return dispatcher


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


async def test_message_update_fanout_strips_viewer_fields(monkeypatch, recording_ws):
    response = _actor_response(monkeypatch)

    # Non-vacuous: the actor's own body really carries each private field, so
    # the sanitizer is doing work rather than nulling already-empty values.
    raw = response.model_dump()
    assert raw["poll_results"] is not None
    assert raw["poll_results"]["total_votes"] == 3
    assert [o["vote_count"] for o in raw["poll_results"]["options"]] == [2, 1]
    assert raw["poll"]["total_votes"] == 3
    assert raw["read"] is True
    assert raw["status"] == "read"
    assert raw["reactions"][0]["me"] is True

    mixin = broadcast_mod.BroadcastMixin()
    await mixin._broadcast_message_update(
        response, 55, None, None, SimpleNamespace(user_id=AUTHOR), conversation_id=55
    )
    await _drain_broadcast_tasks()

    assert len(recording_ws.dispatched) == 1
    event, recipients = recording_ws.dispatched[0]
    assert set(recipients) == {11, 22}
    data = event.data

    # Viewer-specific poll data is gone ...
    assert data["poll_results"] is None
    assert data["poll"]["total_votes"] is None
    assert all(o["vote_count"] is None for o in data["poll"]["options"])
    # ... read/reaction state is neutralized ...
    assert data["read"] is False
    assert data["read_by"] == []
    assert data["status"] is None
    assert all(r["me"] is False for r in data["reactions"])
    # ... but the non-sensitive skeleton survives for the client merge.
    assert data["poll"]["question"] == "Secret poll"
    assert [o["id"] for o in data["poll"]["options"]] == ["10", "11"]
    assert data["content"] == "vote!"
    assert data["reactions"][0]["count"] == 5
    # The actor's returned HTTP response is untouched by the sanitizer.
    assert response.poll_results.total_votes == 3
    assert response.read is True


async def test_message_create_fanout_strips_viewer_fields(monkeypatch, recording_ws):
    response = _actor_response(monkeypatch)
    assert response.model_dump()["poll_results"] is not None

    mixin = broadcast_mod.BroadcastMixin()
    await mixin._broadcast_message_create(
        response,
        None,
        55,
        None,
        None,
        SimpleNamespace(user_id=AUTHOR),
        conversation_id=55,
    )
    await _drain_broadcast_tasks()

    assert len(recording_ws.dispatched) == 1
    data = recording_ws.dispatched[0][0].data
    assert data["poll_results"] is None
    assert data["poll"]["total_votes"] is None
    assert data["read"] is False
    assert data["status"] is None
    assert all(r["me"] is False for r in data["reactions"])
