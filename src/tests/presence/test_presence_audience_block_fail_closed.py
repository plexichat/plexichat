"""Regression: presence-audience block filters must fail CLOSED.

``PRESENCE_UPDATE`` frames carry a subject's real online/idle status and are
published with an *explicit* recipient list, so the gateway router's implicit
DM block filter never sees them. Two audience builders resolve that list and
apply the two-way block gate themselves:

* ``src.api.routes.presence._get_presence_targets`` (REST update, WS
  presence_update and connect dispatch); and
* ``src.api.websocket.gateway._dispatch_offline_presence`` (disconnect
  offline dispatch, which builds its own audience).

Both used to swallow a failure of ``get_all_blocked_ids`` and continue with
the *unfiltered* audience, broadcasting the subject's presence to users who
blocked them or whom they blocked whenever the authoritative block lookup
errored. Withholding delivery on an unverifiable audience is the only safe
behaviour; the typing and DM-recipient filters already do exactly that.
"""

import asyncio
from unittest.mock import Mock

import pytest

import src.api as api
import src.api.routes.presence as presence_routes
import src.api.websocket.gateway as gateway_module

SUBJECT = 8101
FRIEND = 8102
SHARED = 8103
BLOCKER = 8104


class _Relationships:
    """Friend/graph stub whose two-way block lookup can be made to fail."""

    def __init__(self, *, fail=False, blocked=()):
        self.fail = fail
        self.blocked = set(blocked)

    def get_friend_ids(self, _user_id):
        return [FRIEND]

    def get_all_blocked_ids(self, _user_id):
        if self.fail:
            raise RuntimeError("authoritative block store unavailable")
        return list(self.blocked)


class _Servers:
    def get_all_shared_member_ids(self, _user_id):
        return [SHARED]

    def get_member_user_ids(self, _server_id, exclude_user_id=None):
        return [SHARED]


@pytest.fixture(autouse=True)
def _clear_presence_target_cache():
    """Drop warm presence-target verdicts before/after each test."""
    from src.core.database import invalidate_cached

    key = f"presence_targets:int:{SUBJECT}"
    invalidate_cached(key)
    yield
    invalidate_cached(key)


def _wire(monkeypatch, relationships):
    monkeypatch.setattr(api, "get_relationships", lambda: relationships)
    monkeypatch.setattr(api, "get_servers", lambda: _Servers())


@pytest.mark.presence
@pytest.mark.security
def test_presence_target_block_lookup_failure_withholds_audience(monkeypatch):
    """A failing two-way block lookup must not broadcast to anyone."""
    _wire(monkeypatch, _Relationships(fail=True))

    assert presence_routes._get_presence_targets(SUBJECT) == []


@pytest.mark.presence
@pytest.mark.security
def test_presence_target_normal_audience_is_not_silenced(monkeypatch):
    """Non-vacuous control: with a healthy lookup the audience is resolved."""
    _wire(monkeypatch, _Relationships(fail=False, blocked=(BLOCKER,)))

    targets = presence_routes._get_presence_targets(SUBJECT)

    assert FRIEND in targets
    assert SHARED in targets
    # The block gate still removes the blocked party on the happy path.
    assert BLOCKER not in targets


def _run_offline_dispatch():
    dispatcher = Mock()
    dispatcher.dispatch_event = Mock()
    presence = Mock()
    presence.clear_all_typing.return_value = []
    sessions = Mock()
    # The caller unregisters this connection first; 0 remaining connections
    # means the offline transition must actually proceed.
    sessions.get_user_connection_count.return_value = 0

    asyncio.run(
        gateway_module._dispatch_offline_presence(
            SUBJECT,
            presence,
            dispatcher,
            session_manager=sessions,
        )
    )
    return dispatcher


@pytest.mark.presence
@pytest.mark.security
def test_offline_presence_block_lookup_failure_dispatches_nothing(monkeypatch):
    """Disconnect offline presence must not fan out on a failed block lookup."""
    _wire(monkeypatch, _Relationships(fail=True))

    dispatcher = _run_offline_dispatch()

    dispatcher.dispatch_event.assert_not_called()


@pytest.mark.presence
@pytest.mark.security
def test_offline_presence_normal_audience_is_not_silenced(monkeypatch):
    """Non-vacuous control: a healthy lookup still dispatches offline presence."""
    _wire(monkeypatch, _Relationships(fail=False))

    dispatcher = _run_offline_dispatch()

    dispatcher.dispatch_event.assert_called_once()
    args, _kwargs = dispatcher.dispatch_event.call_args
    delivered = set(args[1])
    assert FRIEND in delivered
    assert SHARED in delivered
