"""PERF: get_permissions_batch must bind override ids in bounded chunks.

The batch resolver builds two set-based queries over caller-supplied channel
ids and the caller's role ids:

    channel_id IN (?, ...) AND target_type = 'member' AND target_id = ?
    channel_id IN (?, ...) AND target_type = 'role' AND target_id IN (?, ...)

With a large channel listing the second statement bound every channel and
every role in one shot (2008 / 3518 binds observed), crossing SQLite's
variable ceiling and giving the planner a giant IN list.  Chunking must keep
every statement bounded while returning the identical permission map: role
overrides still accumulate per channel before the position-ordered sort, and
the member override still wins last.

The fake DB records exact binds and only answers overrides for the pairs it
was actually asked about, so a chunking bug that drops a (channel, role) pair
shows up as a wrong verdict rather than being silently masked.
"""

import pytest

from src.core.servers.handlers.roles.permissions import RolePermissionMixin

pytestmark = [pytest.mark.unit, pytest.mark.servers]

MAX_BIND_PARAMS = 500  # repo-wide bulk-id budget (d4905984 / _MAX_BULK_CHUNK)


class _RecordingDB:
    """Answers override queries only for the bound channel/target pairs."""

    def __init__(self, overrides=()):
        self.calls = []
        self._overrides = [dict(row) for row in overrides]

    def fetch_all(self, sql, params=()):
        params = tuple(params)
        self.calls.append((sql, params))
        n_channel = sql.split("channel_id IN (", 1)[1].split(")", 1)[0].count("?")
        is_role = "target_type = 'role'" in sql
        if is_role:
            n_target = sql.split("target_id IN (", 1)[1].split(")", 1)[0].count("?")
            channels = set(params[:n_channel])
            targets = set(params[n_channel : n_channel + n_target])
        else:
            channels = set(params[:n_channel])
            targets = {params[-1]}
        return [
            dict(row)
            for row in self._overrides
            if row["channel_id"] in channels
            and row["target_type"] == ("role" if is_role else "member")
            and row["target_id"] in targets
        ]


class _Manager:
    def __init__(self, role_rows=()):
        self._permission_cache_prefix = "srv_permission:perms:"
        self._role_rows = [dict(r) for r in role_rows]
        self.warmed = []

    def _get_member_role_rows(self, server_id, user_id):
        return [dict(r) for r in self._role_rows]

    def _cache_set(self, prefix, key, perms):
        self.warmed.append((prefix, key, perms))


class _Handler(RolePermissionMixin):
    def __init__(self, db, role_rows=(), base_perms=None):
        self.db = db
        self.manager = _Manager(role_rows)
        self._base_perms = base_perms or {
            "administrator": False,
            "messages.read": False,
        }
        self.permission_calls = []

    def get_permissions(self, user_id, server_id, channel_id=None):
        self.permission_calls.append((user_id, server_id, channel_id))
        return dict(self._base_perms)


def _channels_of(sql, params):
    n = sql.split("channel_id IN (", 1)[1].split(")", 1)[0].count("?")
    return list(params[:n])


def _calls_of(db, target_type):
    return [(sql, params) for sql, params in db.calls if f"'{target_type}'" in sql]


def test_batch_binds_are_bounded_at_large_n():
    """FAILS BEFORE FIX: one statement binds every channel id and role id."""
    channels = list(range(1, 601))
    roles = [{"id": rid, "position": rid} for rid in range(1, 601)]
    db = _RecordingDB()
    handler = _Handler(db, roles)

    result = handler.get_permissions_batch(7, 1, channels)

    assert set(result) == set(channels)
    assert db.calls

    # THE FAILING ASSERTION (before fix): the role query binds 1200 values.
    assert all(len(params) <= MAX_BIND_PARAMS for _, params in db.calls), [
        len(params) for _, params in db.calls
    ]
    # Binds must match placeholders exactly, and no channel may be dropped.
    assert all(sql.count("?") == len(params) for sql, params in db.calls)
    member_channels = {
        cid for sql, p in _calls_of(db, "member") for cid in _channels_of(sql, p)
    }
    role_channels = {
        cid for sql, p in _calls_of(db, "role") for cid in _channels_of(sql, p)
    }
    assert member_channels == set(channels)
    assert role_channels == set(channels)


def test_batch_preserves_role_pairing_and_order_across_chunks():
    """Chunk boundaries must not lose overrides or change whose deny wins."""
    channels = list(range(1, 503))  # spans the first 500-id chunk boundary
    roles = [{"id": rid, "position": rid} for rid in range(1, 503)]
    allow = {"messages.read": True}
    deny = {"messages.read": True}  # deny dict values are "deny this perm" flags
    overrides = [
        # Channel 501 is in the second channel chunk; its two role overrides
        # come from the first and last role chunks. Higher position wins, so
        # role 502's deny must beat role 1's allow.
        {
            "channel_id": 501,
            "target_type": "role",
            "target_id": 1,
            "allow": allow,
            "deny": {},
        },
        {
            "channel_id": 501,
            "target_type": "role",
            "target_id": 502,
            "allow": {},
            "deny": deny,
        },
        # Channel 502 gets a member override applied last (allow wins back).
        {
            "channel_id": 502,
            "target_type": "role",
            "target_id": 2,
            "allow": {},
            "deny": deny,
        },
        {
            "channel_id": 502,
            "target_type": "member",
            "target_id": 7,
            "allow": allow,
            "deny": {},
        },
        # A first-chunk channel still sees its override normally.
        {
            "channel_id": 3,
            "target_type": "role",
            "target_id": 5,
            "allow": allow,
            "deny": {},
        },
    ]
    db = _RecordingDB(overrides)
    handler = _Handler(db, roles)

    result = handler.get_permissions_batch(7, 1, channels)

    assert len(result) == len(channels)
    assert result[501]["messages.read"] is False
    assert result[502]["messages.read"] is True
    assert result[3]["messages.read"] is True
    assert result[1]["messages.read"] is False  # no override -> base default
    assert all(len(params) <= MAX_BIND_PARAMS for _, params in db.calls)
    assert len(handler.manager.warmed) == len(channels)


def test_batch_admin_bypass_strict_ids_and_empty_input_unchanged():
    channels = [1, 2, 3]

    # Administrators bypass override lookups entirely.
    admin = {"administrator": True, "messages.read": True}
    db = _RecordingDB()
    handler = _Handler(db, base_perms=admin)
    result = handler.get_permissions_batch(7, 1, channels)
    assert result == {cid: admin for cid in channels}
    assert db.calls == []
    assert handler.permission_calls == [(7, 1, None)]

    # Non-integer role ids are still refused before any override lookup.
    db = _RecordingDB()
    handler = _Handler(db, role_rows=[{"id": "not-an-int", "position": 1}])
    with pytest.raises(ValueError, match="integers"):
        handler.get_permissions_batch(7, 1, channels)
    assert db.calls == []

    # Empty batches issue no work at all.
    db = _RecordingDB()
    handler = _Handler(db)
    assert handler.get_permissions_batch(7, 1, []) == {}
    assert db.calls == []
    assert handler.permission_calls == []
