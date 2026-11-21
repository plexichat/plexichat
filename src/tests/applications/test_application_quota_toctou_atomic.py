"""TOCTOU regression for the per-user application cap.

``AppCRUDMixin.create_application`` used to run a separate ``SELECT COUNT(*)``
for ``max_applications_per_user`` and *then* issue a separate bare ``INSERT``.
Two concurrent creators could both read a below-cap count before either
committed and both insert, pushing a user past their application quota — the
same bypass class already fixed for emoji/sticker/sound/webhook/role/invite.

The cap is now enforced by a single atomic guarded INSERT (plus a PostgreSQL
advisory xact lock). These tests prove the authoritative guard still rejects
the overflow even when the advisory count read is stale (the losing-racer
interleaving), and ratchet the source so the guarded INSERT cannot silently
regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.applications.exceptions import ApplicationLimitError

pytestmark = pytest.mark.unit

_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "core/applications/manager/application_crud.py"
)
# Only the early advisory COUNT uses this `as count` spelling; the final
# assertion helper below uses `AS c` so it reads the real post-call count.
_ADVISORY_COUNT_SQL = "COUNT(*) as count FROM app_applications"


def _count(app_manager, owner_id):
    """Read the real (post-call) application count, bypassing any stale probe."""
    return app_manager._db.fetch_one(
        "SELECT COUNT(*) AS c FROM app_applications WHERE owner_id = ?",
        (owner_id,),
    )["c"]


def test_guarded_insert_enforces_application_cap_when_advisory_count_is_stale(
    db, app_manager, test_user, monkeypatch
):
    """A stale below-cap count read cannot push a user over their app cap.

    The old code's only guard was the advisory ``SELECT COUNT(*)``. Simulate
    the losing racer whose read happened before the winning creator committed:
    the count query always returns 0. With the authoritative atomic INSERT
    guard in place the overflow row is still refused and the cap holds exactly.
    If the guarded INSERT were reverted to a bare INSERT, the final assertion
    would see one row too many and this test would fail.
    """
    limit = 2
    app_manager._config["max_applications_per_user"] = limit
    for i in range(limit):
        app_manager.create_application(test_user.id, f"Seed App {i}")

    assert _count(app_manager, test_user.id) == limit

    real_fetch_one = app_manager._db.fetch_one

    def stale_count(sql, params=()):
        if isinstance(sql, str) and _ADVISORY_COUNT_SQL in sql:
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(app_manager._db, "fetch_one", stale_count)

    with pytest.raises(ApplicationLimitError):
        app_manager.create_application(test_user.id, "Overflow App")

    assert _count(app_manager, test_user.id) == limit


def test_create_application_uses_guarded_insert_source_invariant():
    """Ratchet: create_application must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory-lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_APP_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO APP_APPLICATIONS" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subquery missing from INSERT"

    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_application"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_APP_INSERT_SQL" in rendered, "guarded INSERT not used"
