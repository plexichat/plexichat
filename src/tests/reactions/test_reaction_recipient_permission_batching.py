"""Reaction fan-out: per-member permission lookups must be batched.

``ReactionPermissionsMixin.get_participant_ids`` is on the hot path for every
reaction event, read-receipt ACK scope, channel membership probe and websocket
dispatch.  For a server-channel conversation it extends the raw participant set
with every server member who can view the channel.  Before the fix that meant
one ``has_permission`` call -- and, on a cold permission snapshot, one DB/cache
round trip -- per member in the roster: a 200-member server paid 200 lookups on
every reaction.

The fix resolves the whole audience with bounded ``IN`` queries keyed by the
candidate ids. This test pins the reaction fan-out to that bulk path: growing
the roster from 2 to 64 members must NOT grow the number of permission queries,
and the audience (output) must stay identical. A replaced ``has_permission``
still uses the original per-member gate, so custom authz is never bypassed.
"""

from __future__ import annotations

import json

from src.core.reactions.manager.permissions import ReactionPermissionsMixin
from src.core.servers.manager.permission_ops import PermissionOpsMixin

SERVER_ID = 5
CHANNEL_ID = 50
CONVERSATION_ID = 500
OWNER_ID = 900_000
ROLE_ID = 7
MESSAGE_ID = 1


def _member_rows(count):
    return [
        {
            "id": 1000 + i,
            "user_id": 2000 + i,
            "server_id": SERVER_ID,
        }
        for i in range(count)
    ]


class _CountingDb:
    """Fake DB that records statements and serves the handful of tables used."""

    def __init__(self, member_rows):
        self.member_rows = member_rows
        self.queries = []
        self.by_mid = {row["id"]: row for row in member_rows}

    def fetch_one(self, sql, params):
        self.queries.append(sql)
        if "from srv_servers" in sql.lower():
            return {"owner_id": OWNER_ID}
        if "from srv_channels" in sql.lower():
            return {"id": CHANNEL_ID}
        if "from msg_messages" in sql.lower():
            return {"conversation_id": CONVERSATION_ID}
        if "from msg_conversations" in sql.lower():
            return {
                "metadata": json.dumps(
                    {"server_id": SERVER_ID, "channel_id": CHANNEL_ID}
                )
            }
        return None

    def fetch_all(self, sql, params):
        self.queries.append(sql)
        low = sql.lower()
        if "from msg_participants" in low:
            # No existing participants: every member needs a permission check.
            return []
        if "from srv_members" in low:
            # Reaction fan-out fetches the whole roster with one server param;
            # the bulk resolver fetches a bounded id slice with server + ids.
            if len(params) == 1:
                return [dict(r) for r in self.member_rows]
            wanted = set(params[1:])
            return [dict(r) for r in self.member_rows if r["user_id"] in wanted]
        if "from srv_roles" in low:
            mids = set(params)
            grants = []
            for mid in mids:
                row = self.by_mid.get(int(mid))
                if row:
                    grants.append(
                        {
                            "id": ROLE_ID,
                            "member_id": int(mid),
                            "position": 0,
                            "permissions": json.dumps({"messages.read": True}),
                        }
                    )
            return grants
        if "srv_channel_overrides" in low:
            return []
        return []

    @property
    def permission_queries(self):
        return [
            q
            for q in self.queries
            if "srv_members" in q
            or "srv_roles" in q
            or "srv_channel_overrides" in q
            or "srv_servers" in q
            or "srv_permissions" in q
        ]


class _RoleHandler:
    """Per-item grant used by the legacy path; one DB query per call."""

    def __init__(self, db):
        self._db = db

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self._db.fetch_one(
            "SELECT 1 FROM srv_permissions WHERE user_id = ?", (user_id,)
        )
        return True


class _Servers(PermissionOpsMixin):
    """Minimal manager with the stock (canonical) has_permission delegation."""

    def __init__(self, db):
        self._db = db
        self.role_handler = _RoleHandler(db)


class _Reactions(ReactionPermissionsMixin):
    def __init__(self, db, servers):
        self._db = db
        self._servers = servers


def _run(count):
    db = _CountingDb(_member_rows(count))
    reactions = _Reactions(db, _Servers(db))
    ids = reactions.get_participant_ids(CONVERSATION_ID)
    return ids, len(db.permission_queries)


def test_permission_query_count_is_flat_as_roster_grows():
    counts = {}
    for n in (2, 8, 64):
        ids, queries = _run(n)
        counts[n] = queries
        assert ids == [2000 + i for i in range(n)]

    # Before the fix: 2 members -> 2 permission queries, 64 -> 64 (linear).
    # After the fix: a fixed handful of set-based queries regardless of N.
    assert counts[2] == counts[8] == counts[64], counts


def test_large_roster_is_bounded_by_chunk_count_not_member_count():
    _, small_queries = _run(64)
    ids, large_queries = _run(1200)
    assert len(ids) == 1200
    # 500-id chunks: cost grows with ceil(N / 500), never with N itself.
    assert large_queries < 4 * small_queries, (small_queries, large_queries)


class _FailingDb(_CountingDb):
    def fetch_all(self, sql, params):
        if "srv_roles" in sql.lower():
            raise RuntimeError("role table unavailable")
        return super().fetch_all(sql, params)


def test_lookup_error_fails_closed():
    db = _FailingDb(_member_rows(4))
    reactions = _Reactions(db, _Servers(db))
    assert reactions.get_participant_ids(CONVERSATION_ID) == []


class _TypeConfusionDb(_CountingDb):
    def fetch_all(self, sql, params):
        rows = super().fetch_all(sql, params)
        if "srv_members" in sql.lower() and len(params) == 1:
            # A bool id is NOT a valid snowflake; it must never be bound as 1
            # and never become a reaction recipient.
            rows.append({"id": 9999, "user_id": True, "server_id": SERVER_ID})
            rows.append({"id": 9998, "user_id": "2000", "server_id": SERVER_ID})
        return rows


def test_strict_int_coercion_and_viewer_scoping():
    db = _TypeConfusionDb(_member_rows(2))
    reactions = _Reactions(db, _Servers(db))
    ids = reactions.get_participant_ids(CONVERSATION_ID)
    # The bool row is dropped entirely; the string alias of an existing user
    # is de-duplicated to one viewer-scoped id. No id outside the roster can
    # appear in the result.
    assert ids == [2000, 2001]
