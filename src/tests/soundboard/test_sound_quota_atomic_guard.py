"""Concurrency/TOCTOU regression for the per-server soundboard cap.

``SoundboardManager.upload_sound`` used to run a separate ``SELECT COUNT(*)``
quota check and *then* a bare ``INSERT``. Two concurrent creators could both
read a below-cap count and both insert, pushing a server over
``max_sounds_per_server`` — a TOCTOU quota bypass. The cap is now enforced by a
single atomic guarded INSERT (plus a PostgreSQL advisory lock). These tests
prove the authoritative guard cannot be bypassed by the interleaving that used
to defeat the early check, and ratchet the source so the guarded INSERT cannot
silently regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.soundboard.exceptions import SoundLimitError
from src.core.soundboard.models import SoundFormat

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/soundboard/manager.py"


def _count(db, server_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM soundboard_sounds WHERE server_id = ?",
        (server_id,),
    )["c"]


def _seed_server(db):
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (1, 'Test', 1, 1000, 1000)"
    )
    db.execute(
        "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
        "VALUES (1, 1, 1, 1000, 1000)"
    )


def test_atomic_insert_enforces_cap_when_early_check_is_missed(
    soundboard_manager, test_db, monkeypatch
):
    """A creator that slips past the advisory check cannot exceed the cap.

    The TOCTOU interleaving is that a second upload's early count read happens
    before the first upload commits. Simulate the worst case deterministically:
    fill the server to the cap, then force the early check to report a stale
    below-cap count so only the authoritative guarded INSERT can reject the
    overflow.
    """
    limit = 2
    monkeypatch.setitem(soundboard_manager._config, "max_sounds_per_server", limit)
    _seed_server(test_db)

    for index in range(limit):
        soundboard_manager.upload_sound(
            1, 1, f"seed_{index}", SoundFormat.MP3, f"url_{index}", 1000, 1.0
        )
    assert _count(test_db, 1) == limit

    real_fetch_one = test_db.fetch_one

    def stale_count(sql, params=None):
        # A losing racer sees the pre-insert count and passes the early check.
        if isinstance(sql, str) and "COUNT(*) as count FROM soundboard_sounds" in sql:
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(test_db, "fetch_one", stale_count)

    with pytest.raises(SoundLimitError):
        soundboard_manager.upload_sound(
            1, 1, "overflow", SoundFormat.MP3, "url_overflow", 1000, 1.0
        )

    # The guarded INSERT refused the row; the cap still holds exactly.
    assert _count(test_db, 1) == limit


def test_upload_uses_guarded_insert_source_invariant():
    """Ratchet: upload_sound must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_SOUND_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subquery missing from INSERT"

    upload_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "upload_sound"
    )
    rendered = ast.dump(upload_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_SOUND_INSERT_SQL" in rendered, "guarded INSERT not used"
