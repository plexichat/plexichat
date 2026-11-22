"""Regression: channel-view recipient filtering in mention fan-out is batched.

``NotificationCreatorMixin.create_notifications`` gates every channel-scoped
recipient on ``has_permission(user, server, 'channels.view', channel)``. On a
cold permission snapshot that resolver re-reads the server owner, the member
row, the member's roles and the channel's overrides -- once *per recipient*.
A role/@everyone mention in a large server therefore paid an N+1 query storm
proportional to the audience (measured before the fix: 15 reads for 5
recipients, 60 for 20 -- exactly 3 per recipient, linear).

The fix resolves the whole audience with ``get_channel_view_user_ids``: a
bounded ``IN (...)`` prefetch that uses the SAME permission helpers and the
SAME fail-closed rules as the per-user path. After the fix the fan-out issues
a constant 5 reads for both 5 and 20 recipients.

Security: the batched verdict is keyed strictly by the ids passed in, so no
recipient can inherit another's grant. The parity tests below assert the
batched set equals the per-user ``has_permission`` set for owners, plain
members, non-members and members denied by channel overrides, and that the
notification fan-out's output is unchanged.
"""

import uuid

import pytest

from src.core.events.router import clear_presence_audience_cache
from src.core.servers.channel_access_cache import get_channel_access_cache
from src.core.servers.manager.member_role_rows_cache import (
    invalidate_member_role_rows_cache,
)
from src.core.servers.manager.server_owner_cache import invalidate_server_owner_cache


def _clear_caches():
    get_channel_access_cache().clear()
    invalidate_member_role_rows_cache()
    invalidate_server_owner_cache()
    clear_presence_audience_cache()


class _QuerySpy:
    """Count reads issued through a database handle while delegating for real."""

    def __init__(self, db, real_fetch_one, real_fetch_all):
        self._db = db
        self._real_fetch_one = real_fetch_one
        self._real_fetch_all = real_fetch_all
        self.queries = []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def fetch_one(self, query, params=None):
        self.queries.append(" ".join(query.split()))
        return self._real_fetch_one(query, params)

    def fetch_all(self, query, params=None):
        self.queries.append(" ".join(query.split()))
        return self._real_fetch_all(query, params)


def _make_server(auth, servers, tag):
    owner = auth.register(
        username=f"cvb_owner_{tag}",
        email=f"cvb_owner_{tag}@example.com",
        password="TestPass123!",
    )
    server = servers.create_server(owner.id, f"cv batch server {tag}")
    channel = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="general",
        channel_type=servers.ChannelType.TEXT,
    )
    return owner, server, channel


def _add_members(auth, servers, server, tag, count):
    members = []
    for i in range(count):
        user = auth.register(
            username=f"cvb_m_{tag}_{i}",
            email=f"cvb_m_{tag}_{i}@example.com",
            password="TestPass123!",
        )
        servers.add_member(server.id, user.id)
        members.append(user)
    return members


def _count_queries(db, notifications, servers, user_ids, server_id, channel_id):
    """Run the fan-out's channel-view filter with a read counter installed."""
    _clear_caches()
    notifications._servers = servers
    real_fetch_one = db.fetch_one
    real_fetch_all = db.fetch_all
    spy = _QuerySpy(db, real_fetch_one, real_fetch_all)
    db.fetch_one = spy.fetch_one
    db.fetch_all = spy.fetch_all
    try:
        allowed = notifications._filter_recipients_by_channel_view(
            set(user_ids), server_id, channel_id
        )
    finally:
        db.fetch_one = real_fetch_one
        db.fetch_all = real_fetch_all
    return len(spy.queries), allowed


@pytest.mark.notifications
def test_channel_view_filter_query_count_is_constant(modules):
    """5 recipients and 20 recipients cost the same number of DB reads."""
    auth, servers, notifications, db = (
        modules.auth,
        modules.servers,
        modules.notifications,
        modules._db,
    )
    tag = uuid.uuid4().hex[:8]
    owner, server, channel = _make_server(auth, servers, tag)
    # Disjoint audiences so warm caches cannot mask the old per-user N+1.
    small = _add_members(auth, servers, server, f"{tag}s", 5)
    large = _add_members(auth, servers, server, f"{tag}l", 20)

    n_small, allowed_small = _count_queries(
        db, notifications, servers, [u.id for u in small], server.id, channel.id
    )
    n_large, allowed_large = _count_queries(
        db, notifications, servers, [u.id for u in large], server.id, channel.id
    )

    # Identical output: every viewer is still allowed.
    assert allowed_small == {u.id for u in small}
    assert allowed_large == {u.id for u in large}
    # Bounded, constant cost independent of audience size (was linear).
    assert n_small == n_large, (n_small, n_large)
    assert n_large <= 6, n_large


@pytest.mark.notifications
def test_channel_view_bulk_matches_per_user_authz(modules):
    """Batched verdicts equal has_permission for owners, members, non-members
    and members denied by member- or role-targeted channel overrides."""
    auth, servers, notifications, db = (
        modules.auth,
        modules.servers,
        modules.notifications,
        modules._db,
    )
    tag = uuid.uuid4().hex[:8]
    owner, server, channel = _make_server(auth, servers, tag)
    plain = _add_members(auth, servers, server, f"{tag}p", 1)[0]
    member_denied = _add_members(auth, servers, server, f"{tag}d", 1)[0]
    role_denied = _add_members(auth, servers, server, f"{tag}r", 1)[0]
    outsider = auth.register(
        username=f"cvb_out_{tag}",
        email=f"cvb_out_{tag}@example.com",
        password="TestPass123!",
    )

    # Member-targeted deny of channels.view.
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        member_denied.id,
        deny={"channels.view": True},
    )
    # Role-targeted deny of channels.view (applied via the default role).
    roles = servers.get_roles(owner.id, server.id)
    default_role = next(r for r in roles if r.is_default)
    servers.set_channel_override(
        owner.id,
        channel.id,
        "role",
        default_role.id,
        deny={"channels.view": True},
    )
    # The role-denied member gets an explicit member allow, which is applied
    # last and therefore wins over the role deny.
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        role_denied.id,
        allow={"channels.view": True},
    )

    ids = [
        owner.id,
        plain.id,
        member_denied.id,
        role_denied.id,
        outsider.id,
    ]

    # Authoritative per-user verdicts (cold caches), the pre-fix behaviour.
    _clear_caches()
    expected = {
        uid
        for uid in ids
        if servers.has_permission(uid, server.id, "channels.view", channel.id)
    }

    # Batched verdicts, plus the fan-out's own filter, must agree exactly.
    _clear_caches()
    batched = servers.get_channel_view_user_ids(ids, server.id, channel.id)
    assert batched == expected

    _, via_fanout = _count_queries(
        db, notifications, servers, ids, server.id, channel.id
    )
    assert via_fanout == expected

    # Sanity-check the scenario actually exercises both allow and deny.
    assert owner.id in expected
    assert plain.id not in expected  # default role denies view here
    assert member_denied.id not in expected
    assert role_denied.id in expected  # member allow beats role deny
    assert outsider.id not in expected  # non-member fails closed


@pytest.mark.notifications
def test_fanout_output_unchanged_with_batched_view_filter(modules):
    """An @everyone fan-out still notifies exactly the users who can view."""
    auth, servers, messaging, notifications = (
        modules.auth,
        modules.servers,
        modules.messaging,
        modules.notifications,
    )
    tag = uuid.uuid4().hex[:8]
    owner, server, channel = _make_server(auth, servers, tag)
    members = _add_members(auth, servers, server, f"{tag}f", 4)
    denied = members[0]
    servers.set_channel_override(
        owner.id,
        channel.id,
        "member",
        denied.id,
        deny={"channels.view": True},
    )
    expected = {u.id for u in members[1:]}

    notifications._servers = servers
    _clear_caches()
    conv = messaging.create_group(
        owner_id=owner.id,
        name=f"cv {tag}",
        participant_ids=[u.id for u in members],
    )
    msg = messaging.send_message(owner.id, conv.id, "@everyone hi")
    notified = notifications.create_notifications(
        message_id=msg.id,
        author_id=owner.id,
        conversation_id=conv.id,
        content="@everyone hi",
        server_id=server.id,
        channel_id=channel.id,
    )

    assert {n.user_id for n in notified} == expected
    assert denied.id not in {n.user_id for n in notified}
