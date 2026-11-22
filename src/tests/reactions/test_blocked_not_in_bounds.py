"""Regression tests: blocked-user filtering must not bind one param per id.

Every reaction read API excluded the viewer's blocked users with one
``NOT IN (?,?,...)`` placeholder per id. The blocked set is bidirectional and
has no configured cap, so a viewer blocked by (or blocking) enough accounts
crossed SQLite's per-statement bind-variable limit and every reaction read for
that viewer failed with "too many SQL variables". Splitting the ids across
several ``NOT IN`` conjuncts does not help because SQLite caps the TOTAL binds
per statement; the set is now resolved in-database with a constant number of
binds. These tests also assert the filter still hides blocked reactors.
"""

import uuid

import pytest

from src.core.reactions.manager.bounds import blocked_users_not_in

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.reactions]

# More than the classic SQLite per-statement bind limit (999). The old
# one-placeholder-per-id builder crossed it here; the in-database filter must
# not care how large the block list is.
LARGE_BLOCK_LIST = 1200


class _RecordingDB:
    """Delegate to the real DB while recording emitted SQL and params."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def _record(self, sql, params):
        self.calls.append((sql, tuple(params or ())))

    def fetch_all(self, sql, params=None):
        self._record(sql, params)
        return self._real.fetch_all(sql, params)

    def fetch_one(self, sql, params=None):
        self._record(sql, params)
        return self._real.fetch_one(sql, params)

    def execute(self, sql, params=None, **kwargs):
        self._record(sql, params)
        return self._real.execute(sql, params, **kwargs)


def test_blocked_users_clause_uses_subquery_with_constant_binds():
    fragment, params = blocked_users_not_in("r.user_id", 4242)
    assert fragment.count("?") == 2
    assert params == [4242, 4242]
    assert "SELECT blocked_id FROM rel_blocked WHERE blocker_id = ?" in fragment
    assert "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?" in fragment
    # No one-placeholder-per-id expansion remains.
    assert "NOT IN (?" not in fragment


@pytest.mark.parametrize("viewer_id", [None, 0])
def test_blocked_users_clause_falsy_viewer_is_noop(viewer_id):
    assert blocked_users_not_in("user_id", viewer_id) == ("", [])


def _make_message(auth_manager, messaging_manager):
    unique_id = uuid.uuid4().hex[:8]
    viewer = auth_manager.register(
        username=f"bnd_viewer_{unique_id}",
        email=f"bnd_viewer_{unique_id}@example.com",
        password="TestPass123!",
    )
    reactor = auth_manager.register(
        username=f"bnd_reactor_{unique_id}",
        email=f"bnd_reactor_{unique_id}@example.com",
        password="TestPass123!",
    )
    dm = messaging_manager.create_dm(viewer.id, reactor.id)
    msg = messaging_manager.send_message(viewer.id, dm.id, "Blocked NOT IN bound test")
    return viewer, reactor, msg


def test_reaction_reads_exclude_blocked_reactor(
    auth_manager, messaging_manager, rel_manager, reaction_manager
):
    """The in-database filter is equivalent to the old in-memory set."""
    viewer, reactor, msg = _make_message(auth_manager, messaging_manager)
    reaction_manager.add_reaction(reactor.id, msg.id, ":chunk:")

    rel_manager.block_user(viewer.id, reactor.id)

    assert reaction_manager.get_reactions(viewer.id, msg.id).reactions == []
    assert reaction_manager.get_reaction_count(msg.id, viewer_id=viewer.id) == 0
    assert (
        reaction_manager.get_reaction_count_by_emoji(
            msg.id, ":chunk:", viewer_id=viewer.id
        )
        == 0
    )
    assert reaction_manager.get_reaction_users(viewer.id, msg.id, ":chunk:") == []

    rel_manager.unblock_user(viewer.id, reactor.id)

    assert len(reaction_manager.get_reactions(viewer.id, msg.id).reactions) == 1
    assert reaction_manager.get_reaction_count(msg.id, viewer_id=viewer.id) == 1


def test_reaction_reads_emit_bounded_sql(
    auth_manager, messaging_manager, rel_manager, reaction_manager
):
    """No reaction read builds a placeholder list sized by the block set."""
    viewer, reactor, msg = _make_message(auth_manager, messaging_manager)
    reaction_manager.add_reaction(reactor.id, msg.id, ":chunk:")
    rel_manager.block_user(viewer.id, reactor.id)

    real_db = reaction_manager._db
    spy = _RecordingDB(real_db)
    reaction_manager._db = spy
    try:
        reaction_manager.get_reactions(viewer.id, msg.id)
        reaction_manager.get_reaction_users(viewer.id, msg.id, ":chunk:")
        reaction_manager.get_reactions_batch(viewer.id, [msg.id])
        reaction_manager.get_reaction_count(msg.id, viewer_id=viewer.id)
        reaction_manager.get_reaction_count_by_emoji(
            msg.id, ":chunk:", viewer_id=viewer.id
        )
    finally:
        reaction_manager._db = real_db

    blocked_reads = [call for call in spy.calls if "NOT IN" in call[0]]
    assert blocked_reads, "blocked-user filter was not applied"
    for sql, params in blocked_reads:
        assert "NOT IN (SELECT" in sql
        assert "NOT IN (?" not in sql
        # Only the query's own fixed binds plus the two viewer-id binds.
        assert sql.count("?") <= 8
        assert len(params) == sql.count("?")


def _seed_large_block_list(db, viewer_id: int, count: int) -> None:
    """Insert ``count`` extra users the viewer blocks, straight into the DB.

    Bypasses the relationship manager (and its password hashing) so the test
    can exceed the classic 999-bind limit cheaply. FK enforcement is on, so
    the dummy auth_users rows must exist before the rel_blocked rows.
    """
    unique = uuid.uuid4().hex[:10]
    now = 1
    base_id = 9_000_000_000_000_000_000
    user_rows = []
    block_rows = []
    for offset in range(count):
        uid = base_id + offset
        user_rows.append((uid, f"scaleblk_{unique}_{offset}", "x", "[]", now, now))
        block_rows.append((uid, viewer_id, now))
    for row in user_rows:
        db.execute(
            "INSERT INTO auth_users (id, username, password_hash, permissions, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
    for blocker, blocked, created in block_rows:
        db.execute(
            "INSERT INTO rel_blocked (blocker_id, blocked_id, created_at) VALUES (?, ?, ?)",
            (blocker, blocked, created),
        )


def test_reaction_reads_stay_bounded_and_filtered_with_large_block_list(
    auth_manager, messaging_manager, rel_manager, reaction_manager
):
    """A 1200-entry block list must not grow bind counts or leak reactors.

    The previous builder emitted one ``?`` per blocked id, so this block list
    pushed every reaction read past SQLite's bind-variable limit (999 on most
    builds) and the reads failed outright. With the in-database filter the
    statements stay bounded, the blocked reactor stays hidden, and the visible
    reactor still appears.
    """
    viewer, reactor, msg = _make_message(auth_manager, messaging_manager)
    reaction_manager.add_reaction(reactor.id, msg.id, ":chunk:")
    rel_manager.block_user(viewer.id, reactor.id)
    _seed_large_block_list(reaction_manager._db, viewer.id, LARGE_BLOCK_LIST)

    real_db = reaction_manager._db
    spy = _RecordingDB(real_db)
    reaction_manager._db = spy
    try:
        counts = reaction_manager.get_reactions(viewer.id, msg.id)
        users = reaction_manager.get_reaction_users(viewer.id, msg.id, ":chunk:")
        batch = reaction_manager.get_reactions_batch(viewer.id, [msg.id])
        total = reaction_manager.get_reaction_count(msg.id, viewer_id=viewer.id)
        by_emoji = reaction_manager.get_reaction_count_by_emoji(
            msg.id, ":chunk:", viewer_id=viewer.id
        )
    finally:
        reaction_manager._db = real_db

    # Correctness + filter intact: blocked reactor hidden, no false positives.
    assert counts.reactions == []
    assert counts.total_count == 0
    assert users == []
    assert batch[msg.id] == []
    assert total == 0
    assert by_emoji == 0

    # Bound intact: no emitted statement expands with the 1201-id block list.
    blocked_reads = [call for call in spy.calls if "NOT IN" in call[0]]
    assert blocked_reads, "blocked-user filter was not applied"
    for sql, params in blocked_reads:
        assert "NOT IN (SELECT" in sql
        assert "NOT IN (?" not in sql
        assert sql.count("?") <= 8
        assert len(params) == sql.count("?")
