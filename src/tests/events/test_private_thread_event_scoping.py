"""Private-thread gateway events must never be broadcast to the whole server.

A THREAD_CREATE / THREAD_UPDATE / THREAD_DELETE payload for a private thread
carries its name, owner and existence. Routing such an event by ``guild_id``
makes the event router expand it to *every* member of the server (the router
resolves a server id to the full member list), bypassing thread membership.
These regressions pin the member-scoped routing added to the emitters.
"""

from __future__ import annotations

import pytest

PRIVATE_THREAD_ID = 111
PUBLIC_THREAD_ID = 444
GUILD_ID = 222
CHANNEL_ID = 333
MEMBERS = [1, 2, 3]


@pytest.fixture
def dispatch_spy(monkeypatch):
    """Capture (event, routing kwargs) passed to events.dispatch."""
    from src.core import events

    captured = []

    monkeypatch.setattr(events, "is_setup", lambda: True)

    def _dispatch(event, **kwargs):
        captured.append((event, kwargs))
        return 1

    monkeypatch.setattr(events, "dispatch", _dispatch)
    return captured


@pytest.fixture
def threads_private(monkeypatch):
    """Present the threads module as set up with a private thread + members."""
    from src.core import threads

    from src.core.threads.models import ThreadType

    monkeypatch.setattr(threads, "is_setup", lambda: True)
    monkeypatch.setattr(
        threads, "get_thread_member_user_ids", lambda tid: list(MEMBERS)
    )
    def _thread_type(tid):
        if tid == PRIVATE_THREAD_ID:
            return ThreadType.PRIVATE.value
        if tid == PUBLIC_THREAD_ID:
            return ThreadType.PUBLIC.value
        return None

    monkeypatch.setattr(threads, "get_thread_type_value", _thread_type)


def _private_payload():
    return {
        "id": str(PRIVATE_THREAD_ID),
        "guild_id": str(GUILD_ID),
        "parent_id": str(CHANNEL_ID),
        "owner_id": "1",
        "name": "secret planning",
        "thread_type": "private",
    }


def test_private_thread_create_is_member_scoped(dispatch_spy, threads_private):
    from src.core.events.gateway_emit import emit_thread_create

    emit_thread_create(_private_payload())

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == MEMBERS
    # Critically: no server-wide routing fallback.
    assert kwargs.get("server_id") is None
    assert kwargs.get("channel_id") is None


def test_private_thread_update_is_member_scoped(dispatch_spy, threads_private):
    from src.core.events.gateway_emit import emit_thread_update

    emit_thread_update(_private_payload())

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == MEMBERS
    assert kwargs.get("server_id") is None


def test_private_thread_delete_is_member_scoped_via_tombstone(
    dispatch_spy, threads_private
):
    from src.core.events.gateway_emit import emit_thread_delete

    # No thread_type passed: the emitter must resolve it from the tombstone.
    emit_thread_delete(PRIVATE_THREAD_ID, guild_id=GUILD_ID, parent_id=CHANNEL_ID)

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == MEMBERS
    assert kwargs.get("server_id") is None


def test_public_thread_keeps_server_routing(dispatch_spy, threads_private):
    from src.core.events.gateway_emit import emit_thread_create

    payload = _private_payload()
    payload["id"] = str(PUBLIC_THREAD_ID)
    payload["thread_type"] = "public"
    emit_thread_create(payload)

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    # Public threads route by server as before (no explicit user_ids).
    assert kwargs.get("user_ids") is None
    assert kwargs.get("server_id") == GUILD_ID
    assert kwargs.get("channel_id") == CHANNEL_ID


def test_private_thread_with_unresolvable_members_fails_closed(
    dispatch_spy, monkeypatch
):
    """No member data must mean 'nobody', never 'all server members'."""
    from src.core import events, threads
    from src.core.events.gateway_emit import emit_thread_create

    monkeypatch.setattr(events, "is_setup", lambda: True)
    monkeypatch.setattr(threads, "is_setup", lambda: False)

    captured = []
    monkeypatch.setattr(
        events,
        "dispatch",
        lambda event, **kwargs: captured.append((event, kwargs)),
    )

    emit_thread_create(_private_payload())

    # An event with no recipients is still dispatched (the router drops it),
    # but it must carry an EMPTY explicit user list and no server fallback.
    assert len(captured) == 1
    _event, kwargs = captured[0]
    assert kwargs.get("user_ids") == []
    assert kwargs.get("server_id") is None
