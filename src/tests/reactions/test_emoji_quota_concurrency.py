"""Concurrency/TOCTOU regression for the per-server custom-emoji cap.

``EmojiOpsMixin.create_custom_emoji`` used to run a ``SELECT COUNT(*)`` quota
check (``_check_emoji_limits``) and *then* open a transaction and INSERT. Two
concurrent creators could both read a below-cap count and both insert, pushing
a server over ``max_emojis_per_server`` / ``max_animated_emojis_per_server`` —
a TOCTOU quota bypass of the same class already fixed for invites.

The cap is now enforced by a single atomic guarded INSERT (plus a PostgreSQL
advisory lock). These tests prove the authoritative guard cannot be bypassed by
the interleaving that used to defeat the early check, and ratchet the source so
the guarded INSERT cannot silently regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.reactions.exceptions import EmojiLimitError

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/reactions/manager/emojis.py"


def _count(reactions, server_id, animated=0):
    return reactions._db.fetch_one(
        "SELECT COUNT(*) AS c FROM react_custom_emoji "
        "WHERE server_id = ? AND animated = ?",
        (server_id, animated),
    )["c"]


def test_atomic_insert_enforces_cap_when_early_check_is_missed(
    users_with_server, sample_image, monkeypatch
):
    """A creator that slips past the advisory check cannot exceed the cap.

    The TOCTOU interleaving is that a second creator's early count read happens
    before a first creator commits. Simulate the worst case deterministically:
    fill the server to the cap, then bypass the advisory early check entirely
    so only the authoritative guarded INSERT can reject the overflow.
    """
    owner, _member, server, _group, _msg, _servers, reactions = users_with_server
    limit = 3

    monkeypatch.setitem(reactions._config, "max_emojis_per_server", limit)
    monkeypatch.setitem(reactions._config, "max_animated_emojis_per_server", limit)

    for index in range(limit):
        reactions.create_custom_emoji(
            owner.id, server.id, f"seed_{index}", sample_image, "image/png"
        )
    assert _count(reactions, server.id, animated=0) == limit

    # The check that used to be the ONLY guard is bypassed — as it is for the
    # losing racer whose read preceded the winner's commit.
    monkeypatch.setattr(reactions, "_check_emoji_limits", lambda *a, **kw: None)

    with pytest.raises(EmojiLimitError):
        reactions.create_custom_emoji(
            owner.id, server.id, "overflow", sample_image, "image/png"
        )

    # The guarded INSERT refused the row; the cap still holds exactly.
    assert _count(reactions, server.id, animated=0) == limit


def test_atomic_insert_enforces_animated_cap_when_early_check_is_missed(
    users_with_server, sample_gif, monkeypatch
):
    """The animated cap is enforced independently by the same guarded INSERT."""
    owner, _member, server, _group, _msg, _servers, reactions = users_with_server
    limit = 2

    monkeypatch.setitem(reactions._config, "max_animated_emojis_per_server", limit)

    for index in range(limit):
        reactions.create_custom_emoji(
            owner.id, server.id, f"aseed_{index}", sample_gif, "image/gif"
        )
    assert _count(reactions, server.id, animated=1) == limit

    monkeypatch.setattr(reactions, "_check_emoji_limits", lambda *a, **kw: None)

    with pytest.raises(EmojiLimitError):
        reactions.create_custom_emoji(
            owner.id, server.id, "aoverflow", sample_gif, "image/gif"
        )

    assert _count(reactions, server.id, animated=1) == limit


def test_create_uses_guarded_insert_source_invariant():
    """Ratchet: create_custom_emoji must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_EMOJI_INSERT_SQL"
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

    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_custom_emoji"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_EMOJI_INSERT_SQL" in rendered, "guarded INSERT not used"
