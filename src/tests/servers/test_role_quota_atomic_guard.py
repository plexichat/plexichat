"""TOCTOU regression for the per-server role cap.

``RoleCrudMixin.create_role`` used to run a separate ``SELECT COUNT(*)`` check
for ``max_roles_per_server`` and *then* issue a separate bare ``INSERT``. Two
concurrent creators could both read a below-cap count before either committed
and both insert, pushing a server past its role quota — the same bypass class
already fixed for emoji/sticker/sound/webhook/invites.

The cap is now enforced by a single atomic guarded INSERT (plus a PostgreSQL
advisory xact lock). These tests prove the authoritative guard still rejects the
overflow even when the advisory count read is stale (the losing-racer
interleaving), and ratchet the source so the guarded INSERT cannot silently
regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.servers.exceptions import ServerLimitReachedError

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/servers/handlers/roles/crud.py"
# Only the early advisory COUNT uses this `as count` spelling; the final
# assertion helper below uses `AS c` so it reads the real post-call count.
_ADVISORY_COUNT_SQL = "COUNT(*) as count FROM srv_roles"


def _count(servers, server_id):
    """Read the real (post-call) live-role count, bypassing any stale probe."""
    return servers._db.fetch_one(
        "SELECT COUNT(*) AS c FROM srv_roles WHERE server_id = ? AND deleted = 0",
        (server_id,),
    )["c"]


def test_guarded_insert_enforces_role_cap_when_advisory_count_is_stale(
    fresh_server, monkeypatch
):
    """A stale below-cap count read cannot push a server over its role cap.

    The old code's only guard was the advisory ``SELECT COUNT(*)``. Simulate the
    losing racer whose read happened before the winning creator committed: the
    count query always returns 0. With the authoritative atomic INSERT guard in
    place, the overflow row is still refused and the cap holds exactly. If the
    guarded INSERT were reverted to a bare INSERT, the final assertion would see
    one row too many and this test would fail.
    """
    server, owner, servers = fresh_server
    limit = 2  # server starts with the default @everyone role
    servers._config["max_roles_per_server"] = limit

    servers.create_role(owner.id, server.id, "Seed Role", {"messages.manage": True})
    assert _count(servers, server.id) == limit

    real_fetch_one = servers._db.fetch_one

    def stale_count(sql, params=()):
        if isinstance(sql, str) and _ADVISORY_COUNT_SQL in sql:
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(servers._db, "fetch_one", stale_count)

    with pytest.raises(ServerLimitReachedError):
        servers.create_role(owner.id, server.id, "Overflow Role")

    assert _count(servers, server.id) == limit


def test_create_role_uses_guarded_insert_source_invariant():
    """Ratchet: create_role must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement (including the live-role ``deleted = 0`` filter so the guard
    matches the outcome the advisory check measures) and the PostgreSQL
    advisory-lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_ROLE_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO SRV_ROLES" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subquery missing from INSERT"
    assert "DELETED = 0" in upper, "guard must count only live roles"

    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_role"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_ROLE_INSERT_SQL" in rendered, "guarded INSERT not used"
