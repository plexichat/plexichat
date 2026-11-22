"""Audience-scoping regressions for gateway events in private threads.

Routing a gateway event by ``guild_id`` lets the event router expand it to
every member of the server. For events whose payload carries private-channel
information (thread name/owner/existence, who is typing, who read a message),
that is an information leak. These tests pin the fail-closed scoping added to
the emitters in :mod:`src.core.events.gateway_emit`.
"""

from __future__ import annotations

import pytest

PRIVATE_THREAD_ID = 111
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


def _threads_setup(monkeypatch, *, type_value="private", members=MEMBERS, raises=False):
    """Present the threads module as configured for a single thread."""
    from src.core import threads

    def _type(tid):
        if raises:
            raise RuntimeError("tombstone lookup failed")
        return type_value if tid == PRIVATE_THREAD_ID else None

    monkeypatch.setattr(threads, "is_setup", lambda: True)
    monkeypatch.setattr(threads, "get_thread_type_value", _type)
    monkeypatch.setattr(
        threads,
        "get_thread_member_user_ids",
        lambda tid: list(members) if tid == PRIVATE_THREAD_ID else [],
    )


def _private_payload(thread_type="private"):
    return {
        "id": str(PRIVATE_THREAD_ID),
        "guild_id": str(GUILD_ID),
        "parent_id": str(CHANNEL_ID),
        "owner_id": "1",
        "name": "secret planning",
        "thread_type": thread_type,
    }


# --- emit_thread_delete fail-closed on resolution failure -------------------


def test_thread_delete_fails_closed_when_type_unresolvable(dispatch_spy, monkeypatch):
    """No type + no threads module must never broadcast to the whole server."""
    from src.core import threads
    from src.core.events.gateway_emit import emit_thread_delete

    monkeypatch.setattr(threads, "is_setup", lambda: False)

    emit_thread_delete(PRIVATE_THREAD_ID, guild_id=GUILD_ID, parent_id=CHANNEL_ID)

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == []
    assert kwargs.get("server_id") is None
    assert kwargs.get("channel_id") is None


def test_thread_delete_fails_closed_when_tombstone_lookup_raises(
    dispatch_spy, monkeypatch
):
    """A failed authoritative lookup scopes to members, never to the server."""
    from src.core.events.gateway_emit import emit_thread_delete

    _threads_setup(monkeypatch, raises=True)

    emit_thread_delete(PRIVATE_THREAD_ID, guild_id=GUILD_ID, parent_id=CHANNEL_ID)

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == MEMBERS
    assert kwargs.get("server_id") is None


# --- authoritative privacy wins over a misleading payload -------------------


def test_thread_create_scoped_when_authoritative_says_private(
    dispatch_spy, monkeypatch
):
    """A 'public' payload cannot broadcast a genuinely private thread."""
    from src.core.events.gateway_emit import emit_thread_create

    _threads_setup(monkeypatch, type_value="private")

    emit_thread_create(_private_payload(thread_type="public"))

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == MEMBERS
    assert kwargs.get("server_id") is None


def test_thread_update_scoped_when_payload_type_unknown(dispatch_spy, monkeypatch):
    """An unrecognized thread_type must fail closed, not route server-wide."""
    from src.core.events.gateway_emit import emit_thread_update

    _threads_setup(monkeypatch, type_value=None)

    emit_thread_update(_private_payload(thread_type="not-a-real-type"))

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == MEMBERS
    assert kwargs.get("server_id") is None


def test_public_thread_keeps_server_routing(dispatch_spy, monkeypatch):
    """Positive evidence of 'public' preserves the legacy routing."""
    from src.core.events.gateway_emit import emit_thread_create

    _threads_setup(monkeypatch, type_value=None)

    emit_thread_create(_private_payload(thread_type="public"))

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") is None
    assert kwargs.get("server_id") == GUILD_ID
    assert kwargs.get("channel_id") == CHANNEL_ID


# --- resolve_channel_event_recipients ---------------------------------------


class _FakeChannel:
    def __init__(self, server_id):
        self.server_id = server_id


class _FakeServers:
    def __init__(self, *, channel=True, members=MEMBERS, permission=True):
        self._channel = _FakeChannel(GUILD_ID) if channel else None
        self._members = members
        self._permission = permission

    def get_channel(self, channel_id, actor_id):
        return self._channel

    def get_member_user_ids(self, server_id):
        return list(self._members)

    def has_permission(self, uid, server_id, permission, channel_id):
        return self._permission and uid in (1, 2, 3)


class _FakeMessaging:
    def get_manager(self):
        return self

    def get_participant_ids(self, channel_id):
        return [5, 6]


class _NoBlocks:
    """Wired relationships source with no block edges.

    resolve_channel_event_recipients applies the two-way block filter to every
    audience and fails closed when a wired block source's lookup errors. These
    tests pin permission/thread/DM scoping, so supply a healthy empty block set
    rather than letting the real, uninitialized relationships module be
    mistaken for a failing authoritative source.
    """

    def get_all_blocked_ids_bulk(self, ids):
        return {}

    def get_all_blocked_ids(self, uid):
        return []


def _patch_api(monkeypatch, servers, messaging=None):
    import src.api as api

    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_relationships", lambda: _NoBlocks())


def test_channel_recipients_filtered_by_permission(monkeypatch):
    """A server channel reaches only members who can view it, never all members."""
    from src.core import threads
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    monkeypatch.setattr(threads, "is_setup", lambda: False)
    _patch_api(monkeypatch, _FakeServers(members=[1, 2, 3, 99]))

    recipients = resolve_channel_event_recipients(
        CHANNEL_ID, guild_id=GUILD_ID, actor_id=1
    )

    # 99 has no channels.view; 1 is the actor and is excluded.
    assert recipients == [2, 3]


def test_channel_recipients_fail_closed_without_permission_api(monkeypatch):
    from src.core import threads
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    monkeypatch.setattr(threads, "is_setup", lambda: False)

    class _NoPermission(_FakeServers):
        has_permission = None

    _patch_api(monkeypatch, _NoPermission())

    assert resolve_channel_event_recipients(CHANNEL_ID, guild_id=GUILD_ID) == []


def test_channel_recipients_scope_private_thread_members(monkeypatch):
    from src.core import threads
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    # The thread id doubles as its channel id on the gateway.
    monkeypatch.setattr(threads, "is_setup", lambda: True)
    monkeypatch.setattr(threads, "get_thread_type_value", lambda tid: "private")
    monkeypatch.setattr(threads, "get_thread_member_user_ids", lambda tid: [1, 2, 3])
    # A private-thread channel must not fall through to the server member list.
    _patch_api(monkeypatch, _FakeServers(members=[1, 2, 3, 99]))

    recipients = resolve_channel_event_recipients(
        PRIVATE_THREAD_ID, guild_id=GUILD_ID, actor_id=1
    )

    assert recipients == [2, 3]


def test_channel_recipients_fall_back_to_dm_participants(monkeypatch):
    from src.core import threads
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    monkeypatch.setattr(threads, "is_setup", lambda: False)
    _patch_api(monkeypatch, _FakeServers(channel=False), _FakeMessaging())

    assert resolve_channel_event_recipients(CHANNEL_ID, actor_id=5) == [6]


# --- emitters use the scoped audience ---------------------------------------


def test_message_ack_emitter_uses_scoped_recipients(dispatch_spy, monkeypatch):
    from src.core.events import gateway_emit

    monkeypatch.setattr(
        gateway_emit, "resolve_channel_event_recipients", lambda *a, **k: [7, 8]
    )

    gateway_emit.emit_message_ack(CHANNEL_ID, 1, message_id=42)

    assert len(dispatch_spy) == 1
    _event, kwargs = dispatch_spy[0]
    assert kwargs.get("user_ids") == [7, 8]
    assert kwargs.get("server_id") is None


def test_typing_emitters_uses_scoped_recipients(dispatch_spy, monkeypatch):
    from src.core.events import gateway_emit

    monkeypatch.setattr(
        gateway_emit, "resolve_channel_event_recipients", lambda *a, **k: [7, 8]
    )

    gateway_emit.emit_typing_start(CHANNEL_ID, 1, username="ann")
    gateway_emit.emit_typing_stop(CHANNEL_ID, 1)

    assert len(dispatch_spy) == 2
    for _event, kwargs in dispatch_spy:
        assert kwargs.get("user_ids") == [7, 8]
        assert kwargs.get("server_id") is None


@pytest.mark.parametrize(
    "emitter,args,kwargs",
    [
        (
            "emit_message_delete_bulk",
            (CHANNEL_ID, [10]),
            {"guild_id": GUILD_ID, "actor_id": 1},
        ),
        (
            "emit_message_reaction_remove_all",
            (CHANNEL_ID, 10),
            {"guild_id": GUILD_ID, "actor_id": 1},
        ),
        (
            "emit_message_reaction_remove_emoji",
            (CHANNEL_ID, 10, "x"),
            {"guild_id": GUILD_ID, "actor_id": 1},
        ),
        ("emit_channel_pins_update", (CHANNEL_ID,), {"guild_id": GUILD_ID}),
        (
            "emit_poll_update",
            (9, 10, "vote"),
            {"actor_id": 1, "channel_id": CHANNEL_ID},
        ),
    ],
)
def test_message_family_emitters_use_scoped_recipients(
    emitter, args, kwargs, dispatch_spy, monkeypatch
):
    """Message-family events in a channel must not reach the whole server."""
    from src.core.events import gateway_emit

    monkeypatch.setattr(
        gateway_emit, "resolve_channel_event_recipients", lambda *a, **k: [7, 8]
    )

    getattr(gateway_emit, emitter)(*args, **kwargs)

    assert len(dispatch_spy) == 1
    _event, dispatch_kwargs = dispatch_spy[0]
    # Explicit audience wins over the legacy server_id routing key.
    assert dispatch_kwargs.get("user_ids") == [7, 8]
