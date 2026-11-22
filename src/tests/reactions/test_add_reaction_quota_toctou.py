"""Reaction add quotas must be enforced atomically with the INSERT.

``ReactionOpsMixin.add_reaction`` used to read the message-wide distinct-emoji
count and the per-user distinct-emoji count in a plain SELECT and only then
issue a bare ``INSERT``. Under PostgreSQL READ COMMITTED two concurrent adds
can both observe a below-cap count, then both insert, pushing the message (or
the user) past its quota -- the same check-then-insert TOCTOU that the custom
emoji quota was hardened against in ``emojis.py``.

These tests force the losing racer's exact interleaving deterministically: the
early quota probe is made to return a stale, below-cap snapshot (as it would
before the winning racer commits), while the guarded INSERT still reads live
table state. Before the atomic guard existed the surplus row slipped through;
with the guard the quota holds. A source ratchet keeps the guard in place.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.reactions.exceptions import ReactionLimitError

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/reactions/manager/reactions.py"


def _dm_with_message(messaging_manager, three_users):
    first, second, _third = three_users
    dm = messaging_manager.create_dm(first.id, second.id)
    return (
        first,
        second,
        messaging_manager.send_message(first.id, dm.id, "quota TOCTOU"),
    )


def _stale_quota(db):
    """Make the early grouped quota probe return a stale below-cap snapshot.

    This models a second transaction whose READ COMMITTED snapshot was taken
    before the first racer committed: it sees an empty/quota-free message even
    though the table already holds the winning racer's row. The authoritative
    guard lives inside the INSERT statement and is deliberately NOT intercepted
    here, so only a real atomic guard can reject the surplus row.
    """
    original = db.fetch_one

    def fetch_one(query, params=None):
        normalized = " ".join((query or "").lower().split())
        if (
            "count(distinct emoji)" in normalized
            and "max(case when emoji" in normalized
        ):
            return {
                "unique_count": 0,
                "emoji_on_message": 0,
                "user_unique_count": 0,
            }
        return original(query, params)

    return fetch_one


def test_message_cap_holds_when_quota_probe_is_stale(
    db, reaction_manager, messaging_manager, three_users, monkeypatch
):
    first, second, msg = _dm_with_message(messaging_manager, three_users)
    monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 1)
    monkeypatch.setitem(reaction_manager._config, "max_unique_reactions_per_user", 50)

    reaction_manager.add_reaction(first.id, msg.id, "\U0001f44d")

    # Simulate the losing racer whose quota snapshot predates the first commit.
    monkeypatch.setattr(db, "fetch_one", _stale_quota(db))
    with pytest.raises(ReactionLimitError):
        reaction_manager.add_reaction(second.id, msg.id, "\U0001f389")

    # The authoritative guard must keep the message at exactly one distinct
    # emoji even though the early probe was fooled.
    rows = db.fetch_all(
        "SELECT DISTINCT emoji FROM react_reactions WHERE message_id = ?", (msg.id,)
    )
    assert [r["emoji"] for r in rows] == ["\U0001f44d"]


def test_user_cap_holds_when_quota_probe_is_stale(
    db, reaction_manager, messaging_manager, three_users, monkeypatch
):
    first, second, msg = _dm_with_message(messaging_manager, three_users)
    monkeypatch.setitem(reaction_manager._config, "max_reactions_per_message", 50)
    monkeypatch.setitem(reaction_manager._config, "max_unique_reactions_per_user", 1)

    reaction_manager.add_reaction(first.id, msg.id, "\U0001f44d")

    monkeypatch.setattr(db, "fetch_one", _stale_quota(db))
    with pytest.raises(ReactionLimitError):
        reaction_manager.add_reaction(first.id, msg.id, "\U0001f525")

    rows = db.fetch_all(
        "SELECT DISTINCT emoji FROM react_reactions WHERE message_id = ? AND user_id = ?",
        (msg.id, first.id),
    )
    assert [r["emoji"] for r in rows] == ["\U0001f44d"]


def test_guarded_insert_still_allows_legitimate_add(
    reaction_manager, messaging_manager, three_users
):
    first, _second, msg = _dm_with_message(messaging_manager, three_users)
    reaction = reaction_manager.add_reaction(first.id, msg.id, "\U0001f44d")
    assert reaction.emoji == "\U0001f44d"
    assert reaction_manager.get_reaction_count(msg.id) == 1


def test_add_reaction_keeps_atomic_guard_source_invariant():
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    guarded = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_REACTION_INSERT_SQL"
            for t in node.targets
        ):
            guarded = node.value
    assert guarded is not None, "atomic guarded reaction INSERT statement missing"

    sql = ast.literal_eval(guarded).upper()
    assert "INSERT INTO" in sql
    assert "SELECT" in sql
    assert "COUNT(" in sql and "DISTINCT" in sql, "quota guard subquery missing"

    add_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "add_reaction"
    )
    rendered = ast.dump(add_fn)
    assert "_ATOMIC_REACTION_INSERT_SQL" in rendered, "guarded INSERT not used by add"
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
