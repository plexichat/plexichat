"""Regression: MESSAGE_ACK read receipts must not cross a DM block.

Read receipts are dispatched with an *explicit* recipient list, which bypasses
the gateway router's DM block filter. Without a route-level gate, a blocked
pair in a shared DM/group still receives each other's ``MESSAGE_ACK`` frames,
leaking read/presence activity across a block that ``send_message`` already
enforces. Typing events got the equivalent fix in ``typing.py``; this suite
pins the same invariant for the read-receipt routes (single and bulk ACK).

Run with: pytest src/tests/api/test_read_receipt_dm_block_audience.py -q
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.read_receipts import (
    _filter_blocked_dm_ack_recipients,
    acknowledge_messages,
    _dispatch_message_ack_broadcast,
)
from src.core.auth.models import AccountType, TokenInfo


def _token(uid):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user{uid}",
        account_type=AccountType.USER,
    )


class _Relationships:
    """Minimal two-way block lookup stub (get_all_blocked_ids is authoritative)."""

    def __init__(self, blocked=None, explode=False):
        self.blocked = set(blocked or [])
        self.explode = explode
        self.lookups = []

    def get_all_blocked_ids(self, user_id):
        self.lookups.append(user_id)
        if self.explode:
            raise RuntimeError("relationship store unavailable")
        return list(self.blocked)


class _Messaging:
    def __init__(self, participants, marked=1):
        self._participants = participants
        self.marked = marked

    def get_manager(self):
        return self

    def get_participant_ids(self, cid):
        return list(self._participants)

    def mark_read(self, uid, cid, mid):
        return self.marked


class _Recorder:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


async def _drain_tasks():
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _wire(monkeypatch, rel, messaging, recorder):
    """Install the fake modules the ACK routes and the block helper consult."""
    monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api_module, "get_servers", lambda: None)
    monkeypatch.setattr(api_module, "get_notifications", lambda: None)
    monkeypatch.setattr(api_module, "get_db", lambda: None)

    # msg_manager.mark_read is reached through src.core.messaging in the
    # single-channel ack path's helper.
    monkeypatch.setattr(
        "src.core.messaging.mark_read", messaging.mark_read, raising=False
    )
    monkeypatch.setattr(
        "src.core.messaging.get_user_message_settings",
        lambda uid: SimpleNamespace(read_receipts_enabled=True),
        raising=False,
    )

    import src.core.relationships as rel_module

    monkeypatch.setattr(rel_module, "_setup_complete", True, raising=False)
    monkeypatch.setattr(rel_module, "_manager", object(), raising=False)
    monkeypatch.setattr(rel_module, "get_all_blocked_ids", rel.get_all_blocked_ids)

    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)


@pytest.mark.api
class TestReadReceiptDmBlockAudience:
    # --- helper-level invariants -------------------------------------------
    def test_blocked_recipient_is_dropped_both_directions(self):
        # get_all_blocked_ids is the two-way set, so a block in either
        # direction shows up here.
        rel = _Relationships(blocked=[2])
        assert _filter_blocked_dm_ack_recipients(1, [2, 3], rel) == [3]

    def test_failed_lookup_fails_closed(self):
        rel = _Relationships(explode=True)
        assert _filter_blocked_dm_ack_recipients(1, [2, 3], rel) == []

    def test_empty_audience_short_circuits(self):
        assert _filter_blocked_dm_ack_recipients(1, [], _Relationships()) == []

    def test_no_relationship_module_leaves_audience_unchanged(self):
        import src.core.relationships as rel_module

        original = rel_module._setup_complete
        try:
            rel_module._setup_complete = False
            assert _filter_blocked_dm_ack_recipients(1, [2, 3]) == [2, 3]
        finally:
            rel_module._setup_complete = original

    # --- single-channel ack route -----------------------------------------
    @pytest.mark.asyncio
    async def test_ack_excludes_blocked_dm_participant(self, monkeypatch):
        rel = _Relationships(blocked=[3])  # actor 1 <-> user 3 blocked
        messaging = _Messaging([1, 2, 3])
        recorder = _Recorder()
        _wire(monkeypatch, rel, messaging, recorder)

        result = await acknowledge_messages(
            "555", message_id=None, current_user=_token(1)
        )
        await _drain_tasks()

        assert result.success is True
        assert len(recorder.dispatched) == 1
        _event, recipients = recorder.dispatched[0]
        assert set(recipients) == {2}, "blocked user 3 must not see the read receipt"
        assert rel.lookups == [1]

    @pytest.mark.asyncio
    async def test_ack_fails_closed_on_block_lookup_error(self, monkeypatch):
        rel = _Relationships(explode=True)
        messaging = _Messaging([1, 2])
        recorder = _Recorder()
        _wire(monkeypatch, rel, messaging, recorder)

        await acknowledge_messages("555", message_id=None, current_user=_token(1))
        await _drain_tasks()

        assert recorder.dispatched == [], (
            "a broken block store must not open the audience"
        )

    # --- bulk ack broadcast helper ----------------------------------------
    @pytest.mark.asyncio
    async def test_bulk_ack_excludes_blocked_dm_participant(self, monkeypatch):
        rel = _Relationships(blocked=[3])
        messaging = _Messaging([1, 2, 3])
        recorder = _Recorder()
        _wire(monkeypatch, rel, messaging, recorder)

        await _dispatch_message_ack_broadcast(
            channel_id=555, message_id=42, current_user_id=1
        )
        await _drain_tasks()

        assert len(recorder.dispatched) == 1
        _event, recipients = recorder.dispatched[0]
        assert set(recipients) == {2}, (
            "blocked user 3 must not see the bulk read receipt"
        )
