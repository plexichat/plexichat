"""Query-count regressions for admin moderation hierarchy checks.

``_ensure_can_target_admin`` guards the platform-wide ban/unban endpoints.
It used to probe ``admin_users`` and then call ``can_manage_admin``, which
fetches the actor's and target's max role positions independently -- three
round trips for a decision made from three scalar values.  The route now
resolves all three in one statement; these tests pin the query count and
verify the batched decision still matches the canonical permission helper.
"""

import pytest

import src.api as api
from src.api.routes.admin import moderation as moderation_routes
from src.core.admin import permissions as perm_core


class _CountingDB:
    """Minimal DB double that records every lookup."""

    def __init__(self, row):
        self.row = row
        self.fetch_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return self.row

    def fetch_all(self, sql, params=()):
        self.fetch_calls.append((sql, tuple(params)))
        return []


def _row(exists=1, current=100, target=50, tuple_row=False):
    if tuple_row:
        return (exists, current, target)
    return {
        "target_exists": exists,
        "current_pos": current,
        "target_pos": target,
    }


@pytest.mark.unit
def test_target_admin_context_is_one_query_and_maps_both_row_shapes():
    for tuple_row in (False, True):
        db = _CountingDB(_row(tuple_row=tuple_row))
        result = moderation_routes._load_target_admin_context(db, 7, 2)

        assert len(db.fetch_calls) == 1
        assert db.fetch_calls[0][1] == (2, 7, 2)
        assert result == (True, 100, 50)


@pytest.mark.unit
def test_target_admin_context_absent_row_defaults_closed():
    db = _CountingDB(None)
    assert moderation_routes._load_target_admin_context(db, 7, 2) == (False, 0, 0)
    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_ensure_can_target_admin_is_a_single_lookup_for_admin_target(monkeypatch):
    db = _CountingDB(_row(exists=1, current=100, target=50))
    monkeypatch.setattr(api, "get_db", lambda: db)

    moderation_routes._ensure_can_target_admin(7, 2)

    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_ensure_can_target_admin_skips_decision_for_non_admin(monkeypatch):
    db = _CountingDB(_row(exists=0))
    monkeypatch.setattr(api, "get_db", lambda: db)

    moderation_routes._ensure_can_target_admin(7, 2)

    assert len(db.fetch_calls) == 1


@pytest.mark.unit
def test_ensure_can_target_admin_rejects_insufficient_position(monkeypatch):
    from fastapi import HTTPException

    db = _CountingDB(_row(exists=1, current=50, target=100))
    monkeypatch.setattr(api, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        moderation_routes._ensure_can_target_admin(7, 2)

    assert exc.value.status_code == 403
    assert len(db.fetch_calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_pos,target_pos,actor,target",
    [
        (100, 50, 7, 2),
        (50, 100, 7, 2),
        (50, 50, 7, 2),
        (100, 100, 7, 7),
        (0, 0, 7, 2),
    ],
)
def test_decision_matches_core_helper(
    monkeypatch, current_pos, target_pos, actor, target
):
    monkeypatch.setattr(
        perm_core,
        "get_admin_max_position",
        lambda db, aid: current_pos if aid == actor else target_pos,
    )
    expected = perm_core.can_manage_admin(object(), actor, target)
    assert (
        moderation_routes._can_manage_admin_at(actor, target, current_pos, target_pos)
        is expected
    )
