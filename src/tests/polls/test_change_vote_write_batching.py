"""change_vote batches its per-option vote writes into one statement.

Before the fix, replacing a vote on an N-option poll issued N separate
``INSERT INTO poll_votes`` statements (one ``execute()`` per option) inside the
transaction. ``vote()`` was already batched with a single ``execute_many()``;
``change_vote()`` was missed. This pins the write count so a future refactor
cannot reintroduce the per-option round trips.
"""

from types import SimpleNamespace

from src.core.polls.manager.voting import VotingMixin

POLL = 2000
USER = 42
OPTION_IDS = [10, 11, 12]  # 3 selections -> 3 writes before, 1 batch after


class _Cursor:
    rowcount = 1

    def close(self):
        pass


class _CountingDB:
    """DB stub that records every poll-vote INSERT statement and its rows."""

    def __init__(self, option_ids):
        self.option_ids = option_ids
        self.vote_inserts = []  # [(kind, rows)]
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append(("fetch_all", sql, tuple(params)))
        if "FROM poll_options" in sql:
            return [{"id": i} for i in self.option_ids]
        return []

    def fetch_one(self, sql, params=()):
        self.calls.append(("fetch_one", sql, tuple(params)))
        if "COUNT(*) AS count FROM poll_options" in sql:
            return {"count": len(self.option_ids)}
        if "FROM poll_voters" in sql:
            return {"1": 1}  # existing voter -> no poll_voters INSERT
        return None

    def begin_transaction(self):
        self.calls.append(("begin_transaction",))

    def execute(self, sql, params=(), auto_commit=True):
        self.calls.append(("execute", sql, tuple(params)))
        if "INSERT INTO poll_votes" in sql:
            self.vote_inserts.append(("execute", [tuple(params)]))
        return _Cursor()

    def execute_many(self, sql, params, auto_commit=True):
        self.calls.append(("execute_many", sql, list(params)))
        if "INSERT INTO poll_votes" in sql:
            self.vote_inserts.append(("execute_many", [tuple(p) for p in params]))
        return _Cursor()

    def commit(self):
        self.calls.append(("commit",))

    def rollback(self):
        self.calls.append(("rollback",))


class _Manager(VotingMixin):
    """Minimal manager: stub the reads, exercise the real change_vote write path."""

    def __init__(self, db):
        self._db = db
        # A DM-shaped message has no conversation channel, so the
        # messages.send gate on poll writes needs no extra grant here.
        self._messaging = SimpleNamespace(
            get_message=lambda uid, mid: SimpleNamespace(id=mid)
        )
        self._generated = 0
        self.emitted = []
        self.invalidated = []

    def _get_timestamp(self):
        return 1000

    def _generate_id(self):
        self._generated += 1
        return 5000 + self._generated

    def get_poll(self, poll_id, user_id):
        return SimpleNamespace(
            id=poll_id,
            message_id=poll_id + 1,
            is_ended=False,
            ends_at=None,
            allow_multiple_choice=True,
        )

    def _invalidate_poll_read_cache(self, *poll_ids):
        self.invalidated.append(poll_ids)

    def _emit_poll_update(self, poll, actor_id, action):
        self.emitted.append((poll.id, actor_id, action))

    def get_results(self, poll_id, user_id):
        return SimpleNamespace(poll_id=poll_id, user_id=user_id)


def test_change_vote_batches_per_option_writes():
    db = _CountingDB(OPTION_IDS)
    mgr = _Manager(db)

    mgr.change_vote(USER, POLL, OPTION_IDS)

    # Exactly one INSERT statement for all N vote rows (was N before the fix).
    assert len(db.vote_inserts) == 1
    kind, rows = db.vote_inserts[0]
    assert kind == "execute_many"
    assert len(rows) == len(OPTION_IDS)

    # Row shape and values are preserved by the batching.
    for row, option_id in zip(rows, OPTION_IDS):
        assert row[1] == POLL
        assert row[2] == option_id
        assert row[3] == USER
        assert row[4] == 1000
    assert len({row[0] for row in rows}) == len(OPTION_IDS), "ids must stay unique"

    # No per-option execute() remains for poll_votes.
    assert not any(
        c[0] == "execute" and "INSERT INTO poll_votes" in c[1] for c in db.calls
    )
