"""Contract test for the dual tombstone sequence on message deletion.

W8-RFA issue 8: the delete route must emit BOTH halves of the tombstone
contract in a pinned order —

    1. MESSAGE_UPDATE with ``deleted=true`` and blanked content
       (live-spread-merge clients and refetching clients converge),
    2. MESSAGE_DELETE (element-removal clients),

and the payloads must be interchangeable in arrival order: a client that
processes MESSAGE_DELETE first and the tombstone MESSAGE_UPDATE second
must converge on the same final state (deleted / removed) instead of
resurrecting the message. The route-level half pins emission order here;
the client-side tolerance both ways is pinned in
plexichat-client/tests/unit/tombstone-order.test.mjs.
"""

import asyncio

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.messages_crud.delete import DeleteMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.events.types import EventType

pytestmark = [pytest.mark.asyncio]


async def _drain_broadcast_tasks():
    """The route broadcasts are fire-and-forget create_task()s; let them run."""
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class RecordingDispatcher:
    """Captures dispatched events without needing a live gateway."""

    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, [str(u) for u in user_ids]))


@pytest.fixture
def wired(messaging_manager, server_manager, auth_manager, monkeypatch):
    """Wire src.api module getters to the real per-test managers."""
    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    # DM scenario: no server scope, recipients come from participants.
    monkeypatch.setattr(api_module, "_servers", None)
    monkeypatch.setattr(api_module, "_auth", auth_manager)


@pytest.fixture
def recording_ws(monkeypatch):
    """Pretend the websocket layer is set up; record every dispatch."""
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: dispatcher)
    return dispatcher


def _token_info(user_id, username):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=username,
        account_type=AccountType.USER,
    )


class TestDualTombstoneSequence:
    async def test_delete_emits_update_tombstone_then_delete(
        self, messaging_manager, two_users, wired, recording_ws
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "to be deleted")

        mixin = DeleteMixin()
        response = await mixin.delete_message(
            str(dm.id), str(msg.id), current_user=_token_info(user1.id, "u1")
        )

        assert response.success is True
        await _drain_broadcast_tasks()
        events = recording_ws.dispatched
        assert len(events) == 2, "exactly one tombstone UPDATE + one DELETE"
        assert [e.event_type for e, _ in events] == [
            EventType.MESSAGE_UPDATE,
            EventType.MESSAGE_DELETE,
        ]
        # Recipients: DM fans out to conversation participants.
        participant_ids = {str(user1.id), str(user2.id)}
        assert set(events[0][1]) == participant_ids
        assert set(events[1][1]) == participant_ids

        update_data = events[0][0].data
        delete_data = events[1][0].data
        assert str(msg.id) == update_data["id"] == delete_data["id"]
        assert update_data["deleted"] is True
        assert update_data["content"] == ""
        assert "channel_id" in delete_data

    async def test_tombstone_payloads_converge_in_either_order(
        self, messaging_manager, two_users, wired, recording_ws
    ):
        """Reducer tolerance both ways: DELETE-then-UPDATE must not
        resurrect the message; UPDATE-then-DELETE must not lose the
        tombstone flag before removal."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        mixin = DeleteMixin()

        def reduce(events, order):
            # Mirrors the live client: MESSAGE_UPDATE spread-merges ONLY
            # when the message is still present (findIndex !== -1);
            # MESSAGE_DELETE removes it from view. ``merged_deleted``
            # records what a MERGED tombstone carried (None if the UPDATE
            # arrived after removal and was correctly ignored).
            state = {"exists": True, "merged_deleted": None}

            def apply_update(d):
                if not state["exists"]:
                    return  # nothing to merge into — no resurrection
                state["merged_deleted"] = bool(d.get("deleted"))

            def apply_delete(d):
                state["exists"] = False

            by_type = {
                EventType.MESSAGE_UPDATE: apply_update,
                EventType.MESSAGE_DELETE: apply_delete,
            }
            seq = (
                [events[0], events[1]]
                if order == "update_first"
                else [events[1], events[0]]
            )
            for event, _ in seq:
                by_type[event.event_type](event.data)
            return state

        # Order A (server contract): tombstone UPDATE -> DELETE.
        msg_a = messaging_manager.send_message(user1.id, dm.id, "order A")
        await mixin.delete_message(
            str(dm.id), str(msg_a.id), current_user=_token_info(user1.id, "u1")
        )
        await _drain_broadcast_tasks()
        state_a = reduce(recording_ws.dispatched, "update_first")
        assert state_a == {"exists": False, "merged_deleted": True}, (
            "UPDATE-first: the tombstone flag is merged before removal"
        )

        # Order B (delayed/out-of-order delivery): same two payloads
        # applied DELETE-first must converge to the same final view.
        recording_ws.dispatched.clear()
        msg_b = messaging_manager.send_message(user1.id, dm.id, "order B")
        await mixin.delete_message(
            str(dm.id), str(msg_b.id), current_user=_token_info(user1.id, "u1")
        )
        await _drain_broadcast_tasks()
        state_b = reduce(recording_ws.dispatched, "delete_first")
        assert state_b == {"exists": False, "merged_deleted": None}, (
            "DELETE-first: the late tombstone UPDATE must be a no-op, "
            "never resurrecting the message"
        )
