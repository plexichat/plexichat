"""Poll read/results paths issue a bounded number of queries.

The viewer's voter flag and the headcount both come from ``poll_voters``; they
are now computed in one conditional-aggregation pass instead of a separate
existence probe plus COUNT. These tests pin that query count and the visibility
semantics so a future refactor cannot leak hidden results.
"""

from src.core.polls.manager.reading import ReadingMixin
from src.core.polls.manager.voting import VotingMixin

POLL = 1000
USER = 42


class _CountingDB:
    def __init__(self, poll_row, option_rows, voter_row, vote_rows):
        self.poll_row = poll_row
        self.option_rows = option_rows
        self.voter_row = voter_row
        self.vote_rows = vote_rows
        self.fetch_one_calls = []
        self.fetch_all_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_one_calls.append((sql, tuple(params)))
        if "FROM poll_polls" in sql:
            return self.poll_row
        if "FROM poll_voters" in sql:
            return self.voter_row
        raise AssertionError(f"unexpected fetch_one: {sql}")

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((sql, tuple(params)))
        if "FROM poll_options" in sql:
            return self.option_rows
        if "FROM poll_votes" in sql:
            return self.vote_rows
        raise AssertionError(f"unexpected fetch_all: {sql}")


def _poll_row(visibility="always"):
    return {
        "id": POLL,
        "message_id": POLL + 1,
        "question": "Which?",
        "question_encrypted": None,
        "created_by": 7,
        "created_at": 100,
        "ends_at": None,
        "ended_at": None,
        "allow_multiple_choice": False,
        "results_visibility": visibility,
    }


_OPTION_ROWS = [
    {"id": 10, "poll_id": POLL, "text": "a", "text_encrypted": None, "position": 0},
    {"id": 11, "poll_id": POLL, "text": "b", "text_encrypted": None, "position": 1},
]


class _Manager:
    get_poll = ReadingMixin.get_poll
    _row_to_poll = ReadingMixin._row_to_poll
    _voter_stats = ReadingMixin._voter_stats
    _has_voted = ReadingMixin._has_voted
    _voter_count = ReadingMixin._voter_count
    _can_view_results = ReadingMixin._can_view_results
    get_results = VotingMixin.get_results
    _encrypt_polls = False

    def __init__(self, db):
        self._db = db

    def _require_messaging(self):
        return self

    def get_message(self, user_id, message_id):
        return {"id": message_id}

    def _get_timestamp(self):
        return 1000


def _db(visibility="always", mine=1, vote_mine=1):
    return _CountingDB(
        poll_row=_poll_row(visibility),
        option_rows=[dict(row) for row in _OPTION_ROWS],
        voter_row={"count": 2, "mine": mine},
        vote_rows=[
            {"option_id": 10, "count": 2, "mine": vote_mine},
            {"option_id": 11, "count": 1, "mine": 0},
        ],
    )


def test_get_poll_uses_one_voter_query():
    db = _db()
    poll = _Manager(db).get_poll(POLL, USER)

    voter_queries = [sql for sql, _ in db.fetch_one_calls if "poll_voters" in sql]
    assert len(voter_queries) == 1
    assert "SUM(CASE WHEN user_id = ?" in voter_queries[0]
    assert poll is not None
    assert poll.total_votes == 2


def test_get_results_issues_bounded_queries():
    db = _db()
    results = _Manager(db).get_results(POLL, USER)

    total = len(db.fetch_one_calls) + len(db.fetch_all_calls)
    # poll row + voter stats (2 fetch_one), options + vote aggregation (2 fetch_all)
    assert total == 4
    assert results.user_voted is True
    assert results.total_votes == 2
    assert [option.vote_count for option in results.options] == [2, 1]


def test_hidden_results_never_expose_counts():
    db = _db(visibility="after_vote", mine=0, vote_mine=0)
    results = _Manager(db).get_results(POLL, USER)

    assert results.user_voted is False
    assert results.total_votes is None
    assert all(option.vote_count is None for option in results.options)
