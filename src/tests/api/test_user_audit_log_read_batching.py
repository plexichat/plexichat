"""Regression tests for the self audit-log read path (GET /users/@me/audit-logs).

The endpoint used to resolve ``server.view_audit_log`` by calling
``get_permissions(user, server)`` once per member server -- an N+1 that costs
up to ~3 DB queries per server on a cold cache -- and then issued an unbounded
``server_id IN (...)`` predicate. It now uses a set-based bulk resolver, bounds
the server fan-out, and keeps the permission-filtered IN-list as the sole
authorization boundary.

These tests assert:
  * parity with the legacy per-server decision (no grants gained or lost),
  * a constant (bounded) query count instead of one lookup per server,
  * bounded pages / IN-list, and
  * no cross-user leak (another user's server can never be selected).
"""

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException

from src.core.auth.models import TokenInfo

U1 = 111111111
U2 = 222222222


def _perms(view=False, admin=False):
    p = {}
    if admin:
        # Real calculate_base_permissions expands administrator to every
        # permission, including the direct view-audit-log bit the route checks.
        p["administrator"] = True
        p["server.view_audit_log"] = True
    if view:
        p["server.view_audit_log"] = True
    return p


class _RecordingDB:
    """Fake DB that records audit queries and filters rows by server_id."""

    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def fetch_all(self, query, params=None):
        self.queries.append((" ".join(query.split()), tuple(params or ())))
        # Return only rows for the authorized server ids (WHERE server_id IN),
        # ordered like the endpoint's ORDER BY created_at DESC.
        server_ids = set(params[:-1]) if params else set()
        rows = [row for row in self._rows if row["server_id"] in server_ids]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)


class _FakeServers:
    """Fake servers module exposing both legacy and batched resolvers."""

    def __init__(self, server_ids, perms, with_bulk=True):
        self._servers = [SimpleNamespace(id=sid) for sid in server_ids]
        self._perms = perms
        self.get_permissions_calls = 0
        self.bulk_calls = 0
        self.bulk_arg_len = 0
        if not with_bulk:
            # Simulate a servers module that predates the batched resolver.
            self.get_permissions_bulk = None  # type: ignore[assignment]

    def get_servers(self, user_id):
        return self._servers

    def get_permissions(self, user_id, server_id):
        self.get_permissions_calls += 1
        return self._perms.get(server_id, {})

    def get_permissions_bulk(self, user_id, server_ids):
        self.bulk_calls += 1
        self.bulk_arg_len = len(server_ids)
        # Mirror the real contract: only ids passed in can be returned.
        return {sid: self._perms.get(sid, {}) for sid in server_ids}


class _Env:
    """Patch api.get_servers/get_db around a direct endpoint call."""

    def __init__(self, servers, db):
        self.servers = servers
        self.db = db

    async def call(self, **kwargs):
        import src.api.routes.features.audit_logs as mod

        orig_gs, orig_db = mod.api.get_servers, mod.api.get_db
        mod.api.get_servers = lambda: self.servers
        mod.api.get_db = lambda: self.db
        try:
            return await mod.get_user_audit_logs(
                limit=25,
                current_user=cast(TokenInfo, SimpleNamespace(user_id=U1)),
                **kwargs,
            )
        finally:
            mod.api.get_servers, mod.api.get_db = orig_gs, orig_db


def _audit_rows(*pairs):
    rows = []
    for sid, action in pairs:
        rows.append(
            {
                "id": sid * 10,
                "server_id": sid,
                "user_id": U2,
                "action": action,
                "target_type": "user",
                "target_id": 999,
                "changes": None,
                "reason": None,
                "created_at": 1700000000000 + sid,
            }
        )
    return rows


@pytest.mark.asyncio
async def test_batched_read_parity_with_legacy_decision(monkeypatch):
    """New path returns exactly the entries the old per-server loop returned."""
    server_ids = [10, 11, 12, 13]
    # 10: direct grant, 11: admin grant, 12/13: no grant.
    perms = {10: _perms(view=True), 11: _perms(admin=True), 12: {}, 13: {}}
    rows = _audit_rows((10, "a"), (11, "b"), (12, "c"), (13, "d"))

    # Legacy reference: per-server get_permissions loop over the same data.
    legacy_servers = _FakeServers(server_ids, perms, with_bulk=False)
    legacy_env = _Env(legacy_servers, _RecordingDB(rows))
    legacy = await legacy_env.call()
    assert legacy_servers.get_permissions_calls == len(server_ids)

    # New batched path must produce an identical result.
    new_servers = _FakeServers(server_ids, perms, with_bulk=True)
    new_env = _Env(new_servers, _RecordingDB(rows))
    new = await new_env.call()

    assert new == legacy
    assert [e["server_id"] for e in new["entries"]] == ["11", "10"]
    assert new_servers.bulk_calls == 1
    assert new_servers.get_permissions_calls == 0


@pytest.mark.asyncio
async def test_query_count_is_bounded_not_per_server():
    """An N-server read uses one batched resolution + one audit query, not N+1."""
    n = 40
    server_ids = list(range(1000, 1000 + n))
    perms = {sid: _perms(view=True) for sid in server_ids}
    rows = _audit_rows(*[(sid, "a") for sid in server_ids])

    servers = _FakeServers(server_ids, perms, with_bulk=True)
    db = _RecordingDB(rows)
    result = await _Env(servers, db).call()

    # Before: 40 get_permissions calls (~120 cold-cache DB queries in prod).
    # After: a single bulk call and a single audit SELECT.
    assert servers.bulk_calls == 1
    assert servers.get_permissions_calls == 0
    assert len(db.queries) == 1
    assert len(result["entries"]) == n

    # The audit query itself carries the scope filter and a bounded page limit.
    sql, params = db.queries[0]
    assert "srv_audit_log" in sql and "server_id IN (" in sql
    assert "LIMIT ?" in sql and params[-1] == 25


@pytest.mark.asyncio
async def test_candidate_server_fanout_is_bounded():
    """Even a misconfigured servers module cannot blow up the IN-list."""
    import src.api.routes.features.audit_logs as mod

    big = list(range(1, mod._MAX_AUDIT_SERVERS + 500))
    perms = {sid: _perms(view=True) for sid in big}
    servers = _FakeServers(big, perms, with_bulk=True)
    db = _RecordingDB([])

    await _Env(servers, db).call()

    assert servers.bulk_arg_len == mod._MAX_AUDIT_SERVERS
    sql, params = db.queries[0]
    # bounded IN-list + the trailing LIMIT parameter
    assert len(params) == mod._MAX_AUDIT_SERVERS + 1


@pytest.mark.asyncio
async def test_no_cross_user_leak_via_other_users_server():
    """A server the caller cannot view is neither listed nor directly reachable."""
    perms = {10: _perms(view=True)}
    # server 99 belongs to U2 and has audit rows, but U1 has no grant on it.
    rows = _audit_rows((10, "a"), (99, "secret"))

    servers = _FakeServers([10], perms, with_bulk=True)
    db = _RecordingDB(rows)
    result = await _Env(servers, db).call()

    assert [e["server_id"] for e in result["entries"]] == ["10"]
    assert all(e["server_id"] != "99" for e in result["entries"])

    # Naming U2's server directly must be rejected, not silently scoped down.
    with pytest.raises(HTTPException) as exc:
        await _Env(_FakeServers([10], perms, with_bulk=True), _RecordingDB(rows)).call(
            server_id="99"
        )
    assert exc.value.status_code == 403


# --- Resolver-level parity (the real PermissionOpsMixin.get_permissions_bulk) ---


class _ResolverDB:
    """Minimal fake of the owner/member/role tables used by get_permissions_bulk."""

    def __init__(self, owners, members, roles):
        self.owners = owners  # server_id -> owner_id
        self.members = members  # (server_id, user_id) -> member_id
        self.roles = roles  # member_id -> list[JSON permission str]
        self.member_server = {mid: sid for (sid, _uid), mid in members.items()}
        self.calls = 0

    def fetch_all(self, query, params=None):
        self.calls += 1
        q = " ".join(query.split())
        if "srv_servers s" in q and "srv_members m" in q:
            uid = params[0]
            sids = params[1:]
            return [
                {"server_id": sid, "owner_id": self.owners[sid], "member_id": mid}
                for sid in sids
                if sid in self.owners and (sid, uid) in self.members
                for mid in [self.members[(sid, uid)]]
            ]
        if "srv_member_roles mr" in q:
            out = []
            for mid in params:
                for perm in self.roles.get(mid, []):
                    out.append(
                        {"server_id": self.member_server[mid], "permissions": perm}
                    )
            return out
        raise AssertionError(f"unexpected query: {q}")


def test_bulk_resolver_matches_calculate_base_permissions():
    """get_permissions_bulk grants exactly what the per-server primitive would."""
    import json

    from src.core.servers.manager.permission_ops import PermissionOpsMixin
    from src.core.servers.permission_utils import calculate_base_permissions

    owners = {10: U1, 11: U2, 12: U2, 13: U2, 14: U2}
    members = {(10, U1): 100, (11, U1): 101, (12, U1): 102, (13, U1): 103}
    roles = {
        100: [],
        101: [json.dumps({"administrator": True})],
        102: [json.dumps({"server.view_audit_log": True})],
        103: [json.dumps({"messages.send": True})],
    }
    db = _ResolverDB(owners, members, roles)

    class _Mgr(PermissionOpsMixin):
        _db = db

    mgr = _Mgr()
    ids = [10, 11, 12, 13, 14]  # 14: server exists but user is not a member
    result = mgr.get_permissions_bulk(U1, ids)

    assert db.calls == 2  # constant, independent of server count
    assert set(result) == {10, 11, 12, 13}
    expected_view = {10: True, 11: True, 12: True, 13: False}
    for sid, expected in expected_view.items():
        assert result[sid].get("server.view_audit_log", False) is expected
    # Every granted set equals the shared primitive's output for that server.
    for sid in result:
        refs = [{"permissions": p} for p in roles.get(members[(sid, U1)], [])]
        assert result[sid] == calculate_base_permissions(refs, owners[sid] == U1)
