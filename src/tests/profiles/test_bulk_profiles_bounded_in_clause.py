"""PERF: bulk profile hydration must bind user ids in bounded chunks.

``ProfileManager.get_bulk_profiles`` accepts a caller-supplied list of user ids
and used to build one ``user_id IN (?, ?, ...)`` for the whole list. Fan-out
paths can hand it more ids than SQLite's 999-variable ceiling allows, so the
whole hydration failed with "too many SQL variables"; below the ceiling the
giant IN list still produced a poor plan. Chunking keeps every statement small
while returning the identical profile map.

The fake DB below records the exact binds of every query and answers as SQLite
would. It deliberately exposes no write API, so any attempted INSERT/UPDATE on
this read path fails loudly.
"""

import pytest

from src.core.profiles.manager import ProfileManager

pytestmark = [pytest.mark.unit]

MAX_BIND_PARAMS = 500  # module-style bulk id budget


class _RecordingDB:
    def __init__(self, rows=()):
        self.calls = []
        self._rows = list(rows)

    def fetch_all(self, query, params=()):
        params = tuple(params)
        self.calls.append((query, params))
        wanted = set(params)
        return [dict(row) for row in self._rows if row["user_id"] in wanted]


def _rows_for(ids):
    return [{"user_id": uid, "bio": f"bio-{uid}", "social_links": None} for uid in ids]


def test_bulk_profiles_binds_are_bounded_at_large_n():
    """FAILS BEFORE FIX: one statement binds every user id."""
    ids = list(range(1, MAX_BIND_PARAMS * 3 + 7))
    db = _RecordingDB(_rows_for(ids))
    mgr = ProfileManager(db)

    bulk = mgr.get_bulk_profiles(ids)

    assert set(bulk) == set(ids)
    assert all(bulk[uid]["bio"] == f"bio-{uid}" for uid in ids)
    assert db.calls

    # THE FAILING ASSERTION (before fix): 1507 binds in a single statement.
    assert all(len(params) <= MAX_BIND_PARAMS for _, params in db.calls), [
        len(params) for _, params in db.calls
    ]
    expected_queries = (len(ids) + MAX_BIND_PARAMS - 1) // MAX_BIND_PARAMS
    assert len(db.calls) == expected_queries
    bound = {uid for _, params in db.calls for uid in params}
    assert set(ids) <= bound, "no id may be silently dropped by chunking"


def test_bulk_profiles_results_and_order_are_identical():
    """Chunk boundaries do not change the returned map or its insertion order."""
    ids = list(range(1, MAX_BIND_PARAMS + 3))
    db = _RecordingDB(_rows_for(ids))
    mgr = ProfileManager(db)

    bulk = mgr.get_bulk_profiles(ids)

    assert list(bulk) == ids
    assert bulk == {uid: bulk[uid] for uid in ids}
    assert all("social_links" in bulk[uid] for uid in ids)


def test_bulk_profiles_missing_users_still_get_defaults():
    """Read-only defaults for absent users survive chunking unchanged."""
    ids = list(range(1, MAX_BIND_PARAMS + 2))
    present = ids[:-1]
    db = _RecordingDB(_rows_for(present))
    mgr = ProfileManager(db)

    bulk = mgr.get_bulk_profiles(ids)

    assert set(bulk) == set(ids)
    assert "bio" in bulk[ids[-1]]
    assert all(len(params) <= MAX_BIND_PARAMS for _, params in db.calls)


def test_bulk_profiles_empty_input_issues_no_query():
    db = _RecordingDB()
    assert ProfileManager(db).get_bulk_profiles([]) == {}
    assert db.calls == []
