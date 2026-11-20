"""Regression tests for batched user-note reads.

Hydrating notes for a page of users used to mean one ``get_note`` call per
target (an N+1 read: N targets, N round trips). The repository now exposes
``get_many``, which binds target ids in bounded ``IN`` chunks and keeps the
author's ``user_id`` predicate in every chunk, and the service exposes
``get_notes_for_targets`` with the same output shape as ``get_note``.

These tests pin both the query reduction and the per-user scoping so neither
can regress back to a per-target loop or an unscoped bulk read.
"""

import pytest

from src.core.auth.repositories.user_notes import (
    MAX_NOTES_READ_CHUNK,
    UserNotesRepository,
)
from src.core.auth.services.user_notes import UserNotesService
from src.core.base import timestamp_ms


class _FakeDB:
    """Minimal DB stub that serves note rows and records every query."""

    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]
        self.queries = []

    def fetch_all(self, query, params=()):
        self.queries.append((query, tuple(params or ())))
        params = tuple(params or ())
        if "target_user_id IN" in query:
            author, targets = params[0], set(params[1:])
            return [
                dict(row)
                for row in self.rows
                if row["user_id"] == author and row["target_user_id"] in targets
            ]
        if "target_user_id = ?" in query:
            author, target = params[0], params[1]
            return [
                dict(row)
                for row in self.rows
                if row["user_id"] == author and row["target_user_id"] == target
            ]
        if "WHERE user_id = ?" in query:
            return [dict(row) for row in self.rows if row["user_id"] == params[0]]
        raise AssertionError(f"unexpected query: {query}")

    def fetch_one(self, query, params=()):
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None


def _row(author, target, seq):
    return {
        "id": 9_000_000_000_000_000_000 + seq,
        "user_id": author,
        "target_user_id": target,
        "note_encrypted": f"cipher-{author}-{target}",
        "created_at": 1_000 + seq,
        "updated_at": 2_000 + seq,
    }


def test_batched_read_replaces_per_target_n_plus_one():
    author = 7
    targets = list(range(100, 140))
    db = _FakeDB(_row(author, target, i) for i, target in enumerate(targets))
    repo = UserNotesRepository(db)

    # Legacy behaviour: one round trip per target.
    legacy = {target: repo.get(author, target) for target in targets}
    legacy_queries = len(db.queries)
    db.queries.clear()

    batched = repo.get_many(author, targets)
    batched_queries = len(db.queries)

    assert batched == legacy
    assert legacy_queries == len(targets)
    assert batched_queries == 1
    assert batched_queries < legacy_queries


def test_batched_read_chunks_bindings_and_keeps_user_scope():
    author = 11
    targets = list(range(200, 223))  # 23 targets, chunk_size 10 -> 3 queries
    db = _FakeDB(_row(author, target, i) for i, target in enumerate(targets))
    repo = UserNotesRepository(db)

    result = repo.get_many(author, targets, chunk_size=10)

    assert len(result) == len(targets)
    assert len(db.queries) == 3
    for sql, params in db.queries:
        assert "WHERE user_id = ?" in sql
        assert "target_user_id IN (" in sql
        # The author is always the first bound parameter, in every chunk.
        assert params[0] == author
        # author + at most ``chunk_size`` target ids.
        assert len(params) <= 11
    assert sorted(len(params) - 1 for _, params in db.queries) == [3, 10, 10]
    assert MAX_NOTES_READ_CHUNK >= 10


def test_batched_read_never_returns_another_authors_notes():
    author, other_author = 31, 32
    targets = [100, 101, 102]
    rows = [_row(author, target, i) for i, target in enumerate(targets)]
    rows += [_row(other_author, target, 10 + i) for i, target in enumerate(targets)]
    db = _FakeDB(rows)
    repo = UserNotesRepository(db)

    result = repo.get_many(author, targets)

    assert set(result) == set(targets)
    assert all(note["user_id"] == author for note in result.values())
    assert all(params[0] == author for _, params in db.queries)


def test_batched_read_empty_targets_issues_no_query():
    db = _FakeDB()
    assert UserNotesRepository(db).get_many(1, []) == {}
    assert db.queries == []


def test_batched_read_deduplicates_targets():
    db = _FakeDB([_row(1, 5, 0)])
    result = UserNotesRepository(db).get_many(1, [5, 5, 5])

    assert set(result) == {5}
    assert len(db.queries) == 1
    assert len(db.queries[0][1]) == 2  # author + one target


def test_batched_read_rejects_non_positive_chunk_size():
    with pytest.raises(ValueError):
        UserNotesRepository(_FakeDB()).get_many(1, [2], chunk_size=0)


def test_service_batched_read_matches_get_note_and_decrypts(monkeypatch):
    author = 41
    targets = [1, 2, 3]
    db = _FakeDB(_row(author, target, i) for i, target in enumerate(targets))
    monkeypatch.setattr(
        "src.core.auth.services.user_notes.decrypt_data",
        lambda value: f"plain:{value}",
    )
    service = UserNotesService(db, encryption_enabled=True)

    legacy = {target: service.get_note(author, target) for target in targets}
    db.queries.clear()
    batched = service.get_notes_for_targets(author, targets)

    assert batched == legacy
    assert all(
        note["note"] == f"plain:cipher-{author}-{target}"
        for target, note in batched.items()
    )
    assert len(db.queries) == 1


def test_batched_read_against_real_database(modules):
    suffix = "batch"
    author = modules.auth.register(
        username=f"batch_author_{suffix}",
        email=f"batch_author_{suffix}@example.com",
        password="TestPass123!",
    )
    other_author = modules.auth.register(
        username=f"batch_other_{suffix}",
        email=f"batch_other_{suffix}@example.com",
        password="TestPass123!",
    )
    targets = [
        modules.auth.register(
            username=f"batch_target_{suffix}_{i}",
            email=f"batch_target_{suffix}_{i}@example.com",
            password="TestPass123!",
        )
        for i in range(8)
    ]

    repo = UserNotesRepository(modules._db)
    base_id = 8_100_000_000_000_000_000
    now = timestamp_ms()
    for i, target in enumerate(targets):
        repo.create(base_id + i, author.id, target.id, f"note-{i}", now, now + i)
    # A note by another author about the same target must never leak in.
    repo.create(base_id + 99, other_author.id, targets[0].id, "other", now, now)

    result = repo.get_many(author.id, [target.id for target in targets], chunk_size=3)

    assert set(result) == {target.id for target in targets}
    assert all(row["user_id"] == author.id for row in result.values())
    assert result[targets[0].id]["note_encrypted"] == "note-0"

    service_result = UserNotesService(
        modules._db, encryption_enabled=False
    ).get_notes_for_targets(author.id, [target.id for target in targets], chunk_size=3)
    assert set(service_result) == set(result)
    assert service_result[targets[0].id]["note"] == "note-0"
