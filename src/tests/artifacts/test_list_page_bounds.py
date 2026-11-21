"""Regression: artifact list pages are bounded at the manager boundary.

The repository only defaulted an *omitted* ``limit``; an explicit negative or
oversized ``limit``/``offset`` reached ``LIMIT ? OFFSET ?`` verbatim. SQLite
reads ``LIMIT -1`` as *unlimited*, so a caller of ``list_with_filters`` (suite
feeds, selftest, tests, or any other internal caller) could dump the whole
artifact table instead of one bounded page.
"""

import src.core.artifacts.manager as manager_mod
from src.core.artifacts.manager import (
    ArtifactManager,
    _MAX_LIST_LIMIT,
    _MAX_LIST_OFFSET,
)


class _SpyDB:
    pass


def _patch_list(monkeypatch):
    captured = {}

    def fake_list_artifacts(db, filters):
        captured["filters"] = filters
        return []

    monkeypatch.setattr(manager_mod, "list_artifacts", fake_list_artifacts)
    return captured


def test_negative_page_values_are_clamped(monkeypatch):
    captured = _patch_list(monkeypatch)
    mgr = ArtifactManager(_SpyDB(), {})

    mgr.list_with_filters({"limit": -1, "offset": -7})

    assert captured["filters"]["limit"] == 0
    assert captured["filters"]["offset"] == 0


def test_oversized_page_values_are_clamped(monkeypatch):
    captured = _patch_list(monkeypatch)
    mgr = ArtifactManager(_SpyDB(), {})

    mgr.list_with_filters({"limit": 10**9, "offset": 10**9})

    assert captured["filters"]["limit"] == _MAX_LIST_LIMIT
    assert captured["filters"]["offset"] == _MAX_LIST_OFFSET


def test_omitted_page_values_keep_the_repository_default(monkeypatch):
    captured = _patch_list(monkeypatch)
    mgr = ArtifactManager(_SpyDB(), {})

    mgr.list_with_filters({"limit": None, "offset": None})

    assert captured["filters"]["limit"] is None
    assert captured["filters"]["offset"] is None


def test_negative_limit_does_not_dump_the_table(db, artifact_builder):
    for _ in range(3):
        artifact_builder(db, author_id=1)

    mgr = ArtifactManager(db, {})
    assert mgr.list_with_filters({"limit": -1}) == []


def test_repository_clamps_limit_and_offset_before_sql(monkeypatch):
    """Direct repository callers are bounded too, not just manager callers.

    ``list_artifacts`` is exported from ``src.core.artifacts`` and is called
    directly by suite feeds. SQLite reads ``LIMIT -1`` as *unlimited* and a
    huge positive limit as "up to N", so leaving either unclamped here lets a
    direct caller dump the whole artifact table despite the manager's own
    page clamp. Assert on the bound SQL parameters so the test pins the actual
    boundary, not the row count of a particular fixture.
    """
    from src.core.artifacts import repository

    captured = {}

    class _SpyDB:
        def fetch_all(self, sql, params=()):
            captured["sql"] = sql
            captured["params"] = tuple(params)
            return []

    # Negative values must become 0 (SQLite would otherwise read -1 as
    # unlimited) and oversized ones must be capped at the shared ceiling.
    repository.list_artifacts(_SpyDB(), {"limit": -1, "offset": -5})
    assert "LIMIT ? OFFSET ?" in captured["sql"]
    assert captured["params"][-2:] == (0, 0)

    repository.list_artifacts(_SpyDB(), {"limit": 10**9, "offset": 10**9})
    assert captured["params"][-2:] == (
        repository._MAX_LIST_LIMIT,
        repository._MAX_LIST_OFFSET,
    )

    # An omitted limit still uses the repository default.
    repository.list_artifacts(_SpyDB(), {})
    assert captured["params"][-2:] == (repository._DEFAULT_LIST_LIMIT, 0)


def test_repository_negative_limit_does_not_dump_the_table(db, artifact_builder):
    for _ in range(3):
        artifact_builder(db, author_id=1)

    from src.core.artifacts.repository import list_artifacts

    # A negative limit used to reach ``LIMIT -1`` (unlimited in SQLite).
    assert list_artifacts(db, {"limit": -1}) == []
