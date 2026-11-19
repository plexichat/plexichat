"""Regression tests for the DM typing-indicator audience.

Typing events are published with an explicit recipient list, which bypasses
the gateway router's DM block filter. Without the route-level gate, a blocked
pair still receives each other's "is typing" frames in a shared DM even though
``send_message`` refuses the same pair.

Run with: pytest src/tests/api/test_typing_block_audience.py -q
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.typing import (
    _filter_blocked_dm_recipients,
    trigger_typing,
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
    def __init__(self, participants):
        self._participants = participants

    def get_participants(self, user_id, cid):
        return [SimpleNamespace(user_id=p) for p in self._participants]


class _Presence:
    def __init__(self):
        self.typing = []

    def start_typing(self, user_id, cid):
        self.typing.append((user_id, cid))


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


@pytest.mark.api
class TestTypedAudienceBlockFilter:
    def test_blocked_recipient_is_dropped_both_directions(self):
        # get_all_blocked_ids is the two-way set, so a block in either
        # direction shows up here.
        rel = _Relationships(blocked=[2])
        assert _filter_blocked_dm_recipients(1, [2, 3], rel) == [3]

    def test_failed_lookup_fails_closed(self):
        rel = _Relationships(explode=True)
        assert _filter_blocked_dm_recipients(1, [2, 3], rel) == []

    def test_empty_audience_short_circuits(self):
        assert _filter_blocked_dm_recipients(1, [], _Relationships()) == []

    @pytest.mark.asyncio
    async def test_typing_route_excludes_blocked_dm_participant(self, monkeypatch):
        """The DM typing broadcast must not reach a blocked participant."""
        rel = _Relationships(blocked=[3])  # actor 1 <-> user 3 blocked
        messaging = _Messaging([1, 2, 3])
        presence = _Presence()
        recorder = _Recorder()

        monkeypatch.setattr(api_module, "get_presence", lambda: presence)
        monkeypatch.setattr(api_module, "get_servers", lambda: None)
        monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)

        import src.core.relationships as rel_module

        monkeypatch.setattr(rel_module, "_setup_complete", True, raising=False)
        monkeypatch.setattr(rel_module, "_manager", object(), raising=False)
        monkeypatch.setattr(rel_module, "get_all_blocked_ids", rel.get_all_blocked_ids)

        monkeypatch.setattr(ws_module, "is_setup", lambda: True)
        monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)

        result = await trigger_typing("555", _token(1))
        await _drain_tasks()

        assert result.success is True
        assert len(recorder.dispatched) == 1
        _event, recipients = recorder.dispatched[0]
        assert set(recipients) == {2}, "blocked user 3 must not see the typing event"
        assert rel.lookups == [1]

    @pytest.mark.asyncio
    async def test_typing_route_fails_closed_on_block_lookup_error(self, monkeypatch):
        """A broken block store must not turn into an open audience."""
        rel = _Relationships(explode=True)
        messaging = _Messaging([1, 2])
        recorder = _Recorder()

        monkeypatch.setattr(api_module, "get_presence", lambda: _Presence())
        monkeypatch.setattr(api_module, "get_servers", lambda: None)
        monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)

        import src.core.relationships as rel_module

        monkeypatch.setattr(rel_module, "_setup_complete", True, raising=False)
        monkeypatch.setattr(rel_module, "_manager", object(), raising=False)
        monkeypatch.setattr(rel_module, "get_all_blocked_ids", rel.get_all_blocked_ids)

        monkeypatch.setattr(ws_module, "is_setup", lambda: True)
        monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)

        await trigger_typing("556", _token(1))
        await _drain_tasks()

        assert recorder.dispatched == []
