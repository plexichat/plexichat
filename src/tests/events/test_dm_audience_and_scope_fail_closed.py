"""Regressions for DM audience leakage and fail-open scope reconstruction.

Two weaknesses in ``resolve_channel_event_recipients``:

1. DM recipients were taken straight from ``get_participant_ids`` without the
   block-relationship filter the ``EventRouter`` applies to the same events.
   A user who blocked the actor (or was blocked by them) still received the
   actor's typing / read-receipt / reaction events.

2. When a caller claimed a server scope (``guild_id``) but the authoritative
   ``get_channel`` lookup failed for the acting user, the unverified guild id
   was used to rebuild a server-wide audience. An unknown or inaccessible
   channel could therefore be announced to every member of the claimed server.

Both paths must fail closed; neither may weaken legitimate routing.
"""

from unittest.mock import MagicMock

import pytest

CHANNEL_ID = 333
GUILD_ID = 222
ACTOR = 1


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    """Force the private-thread fast path off so DM/server paths are exercised."""
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


class _FakeMessaging:
    def __init__(self, participants):
        self._participants = participants

    def get_manager(self):
        return self

    def get_participant_ids(self, channel_id):
        return list(self._participants)


class _FakeRelationships:
    def __init__(self, blocks):
        self._blocks = blocks

    def get_all_blocked_ids(self, user_id):
        return list(self._blocks.get(user_id, []))


def _dm_api(monkeypatch, participants, blocks):
    import src.api as api

    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: _FakeMessaging(participants))
    monkeypatch.setattr(api, "get_relationships", lambda: _FakeRelationships(blocks))
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    return resolve_channel_event_recipients


# --- DM block filtering ------------------------------------------------------


def test_participant_who_blocked_actor_is_excluded(monkeypatch):
    """A participant that blocked the actor must not receive their events."""
    resolve = _dm_api(monkeypatch, [ACTOR, 2, 3], {2: [ACTOR]})

    # 1 is the actor (always excluded); 2 blocked the actor; only 3 remains.
    assert resolve(CHANNEL_ID, actor_id=ACTOR) == [3]


def test_participant_blocked_by_actor_is_excluded(monkeypatch):
    """A participant the actor blocked must not receive their events."""
    resolve = _dm_api(monkeypatch, [ACTOR, 2, 3], {ACTOR: [3]})

    assert resolve(CHANNEL_ID, actor_id=ACTOR) == [2]


def test_dm_participants_without_blocks_are_preserved(monkeypatch):
    """The filter must not silence legitimate DM delivery."""
    resolve = _dm_api(monkeypatch, [ACTOR, 2, 3], {})

    assert resolve(CHANNEL_ID, actor_id=ACTOR) == [2, 3]


def test_dm_participants_kept_when_relationship_source_unavailable(monkeypatch):
    """No relationships module -> keep the existing audience (router parity)."""
    import src.api as api

    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: _FakeMessaging([ACTOR, 2, 3]))
    monkeypatch.setattr(api, "get_relationships", lambda: None)
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    assert resolve_channel_event_recipients(CHANNEL_ID, actor_id=ACTOR) == [2, 3]


# --- Fail-closed server scope ------------------------------------------------


def test_unverifiable_server_scope_does_not_rebuild_server_audience(monkeypatch):
    """A failed channel lookup must not fall back to the caller's guild id."""
    import src.api as api

    servers = MagicMock()
    servers.get_channel.return_value = None
    servers.has_permission.return_value = True
    servers.get_member_user_ids.return_value = [1, 2, 3, 99]

    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)

    # Server-channel audiences now pass through the two-way block gate. Wire a
    # relationship source with no blocks so this test keeps asserting the
    # permission-filtered audience (the gate itself is tested separately).
    class _NoBlocks:
        def get_all_blocked_ids_bulk(self, ids):
            return {int(uid): [] for uid in ids}

    monkeypatch.setattr(api, "get_relationships", lambda: _NoBlocks())
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    result = resolve_channel_event_recipients(
        CHANNEL_ID, guild_id=GUILD_ID, actor_id=ACTOR
    )

    assert result == []
    servers.get_member_user_ids.assert_not_called()


def test_verified_server_channel_still_resolves_viewers(monkeypatch):
    """A resolvable channel keeps the legitimate, permission-filtered audience."""
    import src.api as api

    class _Channel:
        server_id = GUILD_ID

    servers = MagicMock()
    servers.get_channel.return_value = _Channel()
    servers.has_permission.side_effect = lambda uid, sid, perm, cid: uid in (1, 2, 3)
    servers.get_member_user_ids.return_value = [1, 2, 3, 99]

    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)

    # Server-channel audiences now pass through the two-way block gate. Wire a
    # relationship source with no blocks so this test keeps asserting the
    # permission-filtered audience (the gate itself is tested separately).
    class _NoBlocks:
        def get_all_blocked_ids_bulk(self, ids):
            return {int(uid): [] for uid in ids}

    monkeypatch.setattr(api, "get_relationships", lambda: _NoBlocks())
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    result = resolve_channel_event_recipients(
        CHANNEL_ID, guild_id=GUILD_ID, actor_id=ACTOR
    )

    # Actor excluded, 99 has no channels.view; 2 and 3 remain.
    assert result == [2, 3]
