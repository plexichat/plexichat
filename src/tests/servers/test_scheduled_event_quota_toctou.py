"""TOCTOU regression for the per-server scheduled-event capacity bound.

``ScheduledEventManager.create_event`` used to run a separate ``SELECT COUNT(*)``
(``_count_active_events``) and *then* issue a bare ``INSERT``. Two concurrent
creators could both read a below-cap count before either row committed and both
insert, pushing a server past ``max_events_per_server`` -- a quota/capacity TOCTOU
bypass (``generate_recurring_instances`` multiplies the overflow further).

The cap is now enforced by a single atomic guarded INSERT (plus a PostgreSQL
advisory xact lock). These tests prove the authoritative guard still rejects the
overflow when the advisory count read is stale (the losing-racer interleaving
that used to defeat the early check) and ratchet the source so the guarded INSERT
cannot silently regress to a bare INSERT.
"""

import ast
import time
from pathlib import Path

import pytest

from src.core.servers.exceptions import ScheduledEventError

pytestmark = pytest.mark.servers

_SOURCE = (
    Path(__file__).resolve().parents[2] / "core/servers/manager/scheduled_event_ops.py"
)


def _active_count_query():
    return (
        "SELECT COUNT(*) AS count FROM srv_scheduled_events "
        "WHERE server_id = ? AND status IN ('scheduled', 'active')"
    )


def _make_event(servers, server, owner, name: str):
    return servers.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name=name,
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=servers.ScheduledEventType.EXTERNAL,
        location="Test Location",
    )


def test_guarded_insert_enforces_cap_when_count_is_stale(
    server_with_members, monkeypatch
):
    """A creator that slips past the advisory count cannot exceed the cap.

    Deterministically reproduce the losing-racer interleaving: fill the server
    to the cap, then force the advisory count read to report a stale below-cap
    value so only the authoritative guarded INSERT can reject the overflow.
    """
    server, owner, admin_user, member_user, outsider, admin_role, servers = (
        server_with_members
    )
    event_mgr = servers._event_manager
    limit = 2
    monkeypatch.setitem(event_mgr._config, "max_events_per_server", limit)
    real_fetch_one = event_mgr._db.fetch_one

    for index in range(limit):
        assert _make_event(servers, server, owner, f"seed_{index}") is not None
    assert real_fetch_one(_active_count_query(), (server.id,))["count"] == limit

    # The losing racer's advisory read happened before the seeds committed, so
    # it sees zero and passes the early check. Only the guarded INSERT can still
    # reject the row.
    def stale_count(sql, params=None):
        if (
            isinstance(sql, str)
            and "COUNT(*) AS count FROM srv_scheduled_events" in sql
        ):
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(event_mgr._db, "fetch_one", stale_count)

    with pytest.raises(ScheduledEventError, match="Maximum 2 scheduled events"):
        _make_event(servers, server, owner, "overflow")

    # The guarded INSERT refused the row; the cap still holds exactly.
    assert real_fetch_one(_active_count_query(), (server.id,))["count"] == limit


def test_create_event_uses_guarded_insert_source_invariant():
    """Ratchet: create_event must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory-lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_EVENT_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO SRV_SCHEDULED_EVENTS" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subquery missing from INSERT"

    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_event"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_EVENT_INSERT_SQL" in rendered, "guarded INSERT not used"
