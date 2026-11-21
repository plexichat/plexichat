"""The expiry sweep batches its writes and invalidates the read cache once.

Regression for the poll-expiry background sweep.  Before the fix a due batch
of N polls issued N single-row UPDATEs and N *whole-cache* clears (one pair
per poll), so a burst of expiries scaled linearly with N on the write path.
The fix collapses that to a bounded ``execute_many`` plus a single clear, and
guards the write on ``ended_at IS NULL`` so two workers cannot both emit an
event for the same poll.

The tests pin both the query shape (bounded, race-guarded) and the
observable behaviour (every due poll ends, a second sweep is a no-op, and a
worker that loses the race emits nothing).
"""

import pytest
from unittest.mock import patch

from src.core.polls.manager.lifecycle import LifecycleMixin

pytestmark = [pytest.mark.polls, pytest.mark.unit]


class _Cursor:
    rowcount = 1


class _StubDB:
    """Minimal DB stub that records statements and models the guarded UPDATE."""

    def __init__(self, rows, still_open=()):
        self.rows = rows
        self.still_open = set(still_open)
        self.calls = []

    def fetch_all(self, query, params):
        self.calls.append(("fetch_all", query, params))
        if "id IN" in query:
            return [{"id": i} for i in params if i in self.still_open]
        return self.rows

    def execute(self, query, params):
        self.calls.append(("execute", query, params))
        return _Cursor()

    def execute_many(self, query, params_list):
        self.calls.append(("execute_many", query, params_list))
        return _Cursor()


class _Messaging:
    def __init__(self):
        self.lookups = []

    def get_message(self, viewer_id, message_id):
        # The viewer is still the poll creator, so per-poll message access is
        # checked exactly as before -- batching the writes must not widen it.
        self.lookups.append((viewer_id, message_id))
        return type("Message", (), {"conversation_id": 42})()


def _sweep_manager(db, count):
    mgr = LifecycleMixin.__new__(LifecycleMixin)
    mgr._db = db
    mgr._messaging = _Messaging()
    mgr._get_timestamp = lambda: 1000
    invalidations = []
    mgr._invalidate_poll_read_cache = lambda: invalidations.append(1)
    rows = [{"id": i, "message_id": 1000 + i, "created_by": 7} for i in range(count)]
    return mgr, invalidations, rows


def test_expiry_sweep_batches_writes_and_clears_cache_once():
    """N due polls: one bounded UPDATE batch and one cache clear, not N of each."""
    db = _StubDB([])
    mgr, invalidations, rows = _sweep_manager(db, count=200)
    db.rows = rows

    with patch("src.core.events.gateway_emit.emit_poll_update", lambda **kw: None):
        assert mgr.check_expired_polls() == 200

    writes = [c for c in db.calls if c[0] in ("execute", "execute_many")]
    assert len(writes) == 1, "write path must be one batch, not one UPDATE per poll"
    assert writes[0][0] == "execute_many"
    assert len(writes[0][2]) == 200
    assert "ended_at IS NULL" in writes[0][1], "write must be guarded against races"
    assert len(invalidations) == 1
    assert len(mgr._messaging.lookups) == 200, "message authz lookups are per poll"


def test_expiry_sweep_emits_only_for_polls_it_actually_ended():
    """A worker that loses the transition race must not double-emit or miscount."""
    db = _StubDB([], still_open=range(100, 200))
    mgr, invalidations, rows = _sweep_manager(db, count=200)
    db.rows = rows

    emitted = []
    with patch(
        "src.core.events.gateway_emit.emit_poll_update",
        lambda **kw: emitted.append(kw),
    ):
        assert mgr.check_expired_polls() == 100

    assert len(emitted) == 100
    assert all(0 <= event["poll_id"] < 100 for event in emitted)
    assert all(event["actor_id"] == 7 for event in emitted)
    assert len(invalidations) == 1


def test_expiry_sweep_chunk_bounds_are_respected():
    """Large sweeps stay bounded: no statement carries more than 400 ids."""
    db = _StubDB([])
    mgr, _invalidations, rows = _sweep_manager(db, count=900)
    db.rows = rows

    with patch("src.core.events.gateway_emit.emit_poll_update", lambda **kw: None):
        assert mgr.check_expired_polls() == 900

    writes = [c for c in db.calls if c[0] == "execute_many"]
    assert len(writes) == 3
    for _kind, query, params in writes:
        assert len(params) <= 400
    in_queries = [c for c in db.calls if c[0] == "fetch_all" and "id IN" in c[1]]
    assert in_queries
    for _kind, _query, params in in_queries:
        assert len(params) <= 400


def test_executemany_write_invalidates_query_result_cache(db):
    """A batched UPDATE must not leave a cached SELECT serving the old row.

    ``execute()`` clears the query-result cache for writes; ``execute_many()``
    used not to. The poll expiry sweep now writes through ``execute_many``,
    so without this guard the very next sweep re-reads the pre-write "still
    open" snapshot and ends the same polls again.
    """
    poll_id = 991_002_003
    db.execute("DELETE FROM poll_polls WHERE id = ?", (poll_id,))
    db.execute(
        "INSERT INTO poll_polls (id, message_id, question, created_by, "
        "created_at, ends_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (poll_id, 991_002_004, "cache probe", 7, 1000, 2000),
    )
    query = "SELECT ended_at FROM poll_polls WHERE id = ?"

    assert db.fetch_all(query, (poll_id,))[0]["ended_at"] is None
    db.execute_many(
        "UPDATE poll_polls SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
        [(3000, poll_id)],
    )
    assert db.fetch_all(query, (poll_id,))[0]["ended_at"] == 3000


def test_expiry_sweep_end_to_end(
    db, auth_manager, messaging_manager, poll_manager, monkeypatch
):
    """Functional guard: all due polls end and a second sweep does nothing."""
    import uuid
    from unittest.mock import patch
    from src.utils import encryption

    unique_id = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username=f"sweep_user1_{unique_id}",
            email=f"sweep_user1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"sweep_user2_{unique_id}",
            email=f"sweep_user2_{unique_id}@example.com",
            password="TestPass123!",
        )

    dm = messaging_manager.create_dm(user1.id, user2.id)
    polls = []
    for index in range(3):
        msg = messaging_manager.send_message(user1.id, dm.id, f"Sweep {index}")
        polls.append(
            poll_manager.create_poll(
                user_id=user1.id,
                message_id=msg.id,
                question=f"Expire {index}?",
                options=["Yes", "No"],
                duration_hours=1,
            )
        )

    real_now = poll_manager._get_timestamp()
    monkeypatch.setattr(
        poll_manager, "_get_timestamp", lambda: real_now + 2 * 3600 * 1000
    )

    assert poll_manager.check_expired_polls() == 3
    for poll in polls:
        row = db.fetch_one("SELECT ended_at FROM poll_polls WHERE id = ?", (poll.id,))
        assert row is not None and row["ended_at"] is not None
    assert poll_manager.check_expired_polls() == 0
