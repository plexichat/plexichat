"""Regression tests for message-event audience scoping in BroadcastMixin.

A server channel's audience is the set of members that pass ``channels.view``.
If that filter yields nobody, the event must be dropped — it must never fall
back to the raw conversation participant list, which would deliver restricted
channel content to every member of the server. DM/group events may still fall
back to participants, but a two-way block must remove the blocked party from
that audience just as the event router does.
"""

from types import SimpleNamespace

import pytest

from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients


class _ServersWithView:
    """Minimal servers module that grants channels.view to a fixed set."""

    def __init__(self, server_id, members, viewers):
        self.server_id = server_id
        self.members = members
        self.viewers = set(viewers)
        self.permission_calls = []
        self.member_calls = []

    def get_channel(self, channel_id, actor_id):
        return SimpleNamespace(server_id=self.server_id)

    def get_member_user_ids(self, server_id):
        self.member_calls.append(server_id)
        return list(self.members)

    def has_permission(self, user_id, server_id, permission, channel_id):
        self.permission_calls.append((user_id, permission))
        return user_id in self.viewers


class _ServersWithoutPermissionApi:
    """A resolved server channel with no way to verify channels.view."""

    def __init__(self, server_id, members):
        self.server_id = server_id
        self.members = members

    def get_channel(self, channel_id, actor_id):
        return SimpleNamespace(server_id=self.server_id)

    def get_member_user_ids(self, server_id):
        return list(self.members)


class _CountingMessaging:
    """Messaging module that records whether the participant fallback ran."""

    def __init__(self, participants):
        self.participants = participants
        self.calls = []

    def get_participants(self, actor_id, conversation_id):
        self.calls.append((actor_id, conversation_id))
        return [SimpleNamespace(user_id=uid) for uid in self.participants]


@pytest.mark.messaging
class TestBroadcastAudienceHardening:
    def test_restricted_channel_does_not_fall_back_to_participants(self):
        """No channels.view holder means no recipients, not everyone."""
        servers = _ServersWithView(700, [1, 2, 3], viewers=[])
        messaging = _CountingMessaging([1, 2, 3])

        ids, server_id = _resolve_recipients(servers, messaging, 99, 99, actor_id=1)

        assert ids == []
        assert server_id == 700
        assert messaging.calls == [], "server channel leaked into DM fallback"

    def test_partial_viewers_are_the_only_recipients(self):
        servers = _ServersWithView(700, [1, 2, 3], viewers=[2])
        messaging = _CountingMessaging([9, 10])

        ids, server_id = _resolve_recipients(servers, messaging, 99, 99, actor_id=1)

        assert ids == [2]
        assert server_id == 700
        assert messaging.calls == []

    def test_missing_permission_api_fails_closed(self):
        servers = _ServersWithoutPermissionApi(700, [1, 2, 3])
        messaging = _CountingMessaging([1, 2, 3])

        ids, server_id = _resolve_recipients(servers, messaging, 99, 99, actor_id=1)

        assert ids == []
        assert server_id == 700
        assert messaging.calls == []

    def test_server_id_hint_fails_closed_without_permission_api(self):
        class _HintOnly:
            def get_channel(self, channel_id, actor_id):
                raise RuntimeError("system actor is not a member")

        ids, server_id = _resolve_recipients(
            _HintOnly(),
            _CountingMessaging([1, 2]),
            99,
            None,
            actor_id=0,
            server_id_hint=700,
        )

        assert ids == []
        assert server_id == 700

    def test_dm_fallback_still_returns_participants(self, monkeypatch):
        # No block relationships: the two-way filter must keep every
        # participant. Wire an explicit unblocked relationship source so this
        # test does not inherit the real ``src.core.relationships`` module,
        # which is importable but uninitialized in unit tests — its failed
        # authoritative lookup would (correctly) fail closed to an empty
        # audience, masking the DM fallback behaviour under test.
        class _Relationships:
            def get_all_blocked_ids(self, user_id):
                return []

        import src.api as api

        monkeypatch.setattr(api, "get_relationships", lambda: _Relationships())

        messaging = _CountingMessaging([11, 22])

        ids, server_id = _resolve_recipients(None, messaging, 55, 55, actor_id=11)

        assert ids == [11, 22]
        assert server_id is None
        assert messaging.calls == [(11, 55)]

    def test_dm_fallback_excludes_two_way_blocked_participants(self, monkeypatch):
        # 22 blocked the actor (11); the actor blocked 33. Neither may receive
        # the actor's message events even though both are conversation members.
        blocked = {22: [11], 11: [33]}

        class _Relationships:
            def get_all_blocked_ids(self, user_id):
                return blocked.get(user_id, [])

        import src.api as api

        monkeypatch.setattr(api, "get_relationships", lambda: _Relationships())

        messaging = _CountingMessaging([11, 22, 33])

        ids, server_id = _resolve_recipients(None, messaging, 55, 55, actor_id=11)

        assert ids == [11]
        assert server_id is None
