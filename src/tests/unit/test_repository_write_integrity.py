"""Regression tests for repository write/order integrity.

Covers two real weaknesses found in the data-access layer:

* ``auth_user_notes`` has ``UNIQUE(user_id, target_user_id)`` but callers
  raced an ``exists()``/``get()`` check followed by a plain INSERT, so two
  concurrent writes both saw "missing" and one raised ``IntegrityError``.
  The write must be a single atomic upsert.
* ``SavedSearchesRepository.update`` used to issue two independently
  auto-committed UPDATEs (one per field), which could leave a row half
  updated if the second statement failed. It must be one atomic statement.

Also pins deterministic secondary ordering on the affected list reads.
"""

import uuid

from src.core.base import timestamp_ms
from src.core.auth.repositories.user_notes import UserNotesRepository
from src.core.search.repositories.saved_searches import SavedSearchesRepository


class _RecordingDB:
    """Minimal DB stub that records SQL and parameters."""

    def __init__(self):
        self.executed = []
        self.fetched = []

    def execute(self, query, params=()):
        self.executed.append((query, tuple(params or ())))

    def fetch_all(self, query, params=()):
        self.fetched.append((query, tuple(params or ())))
        return []

    def fetch_one(self, query, params=()):
        self.fetched.append((query, tuple(params or ())))
        return None


def test_user_note_create_is_atomic_upsert_with_stable_identity(modules):
    """Two writes for one (user, target) must not race into IntegrityError.

    On the pre-fix code the second ``create`` collided with the table's
    UNIQUE(user_id, target_user_id) constraint and raised, losing the note.
    """
    repo = UserNotesRepository(modules._db)

    suffix = uuid.uuid4().hex[:12]
    author = modules.auth.register(
        username=f"note_author_{suffix}",
        email=f"note_author_{suffix}@example.com",
        password="TestPass123!",
    )
    target = modules.auth.register(
        username=f"note_target_{suffix}",
        email=f"note_target_{suffix}@example.com",
        password="TestPass123!",
    )

    first_id = 8_000_000_000_000_000_001
    second_id = 8_000_000_000_000_000_002
    now = timestamp_ms()

    repo.create(first_id, author.id, target.id, "first", now, now)
    # Simulate the TOCTOU window: another writer reads "missing" and creates.
    # This is a single statement now, so no interleaving can break it.
    repo.create(second_id, author.id, target.id, "second", now + 1, now + 1)

    row = repo.get(author.id, target.id)
    assert row is not None
    assert row["note_encrypted"] == "second"
    assert row["id"] == first_id  # identity stays stable on conflict
    assert row["created_at"] == now  # creation history is preserved
    assert row["updated_at"] == now + 1
    assert len(repo.get_all(author.id)) == 1  # no duplicate rows


def test_user_note_create_uses_single_on_conflict_statement():
    db = _RecordingDB()
    repo = UserNotesRepository(db)

    repo.create(1, 2, 3, "cipher", 4, 5)

    assert len(db.executed) == 1
    sql, params = db.executed[0]
    assert "ON CONFLICT (user_id, target_user_id) DO UPDATE" in sql
    assert "note_encrypted = EXCLUDED.note_encrypted" in sql
    assert "updated_at = EXCLUDED.updated_at" in sql
    # created_at / id must not be overwritten by the conflict branch.
    conflict_branch = sql.split("DO UPDATE SET", 1)[1]
    assert "created_at" not in conflict_branch
    assert "id =" not in conflict_branch
    assert params == (1, 2, 3, "cipher", 4, 5)


def test_saved_search_update_is_one_atomic_statement():
    db = _RecordingDB()
    repo = SavedSearchesRepository(db)

    assert repo.update(10, 20, name="renamed", query="new query") is True

    assert len(db.executed) == 1
    sql, params = db.executed[0]
    assert sql.upper().startswith("UPDATE SAVED_SEARCHES SET")
    assert "name = ?" in sql
    assert "query = ?" in sql
    assert "WHERE id = ? AND user_id = ?" in sql
    assert params == ("renamed", "new query", 10, 20)


def test_saved_search_update_with_no_fields_is_a_noop():
    db = _RecordingDB()
    repo = SavedSearchesRepository(db)

    assert repo.update(10, 20) is False
    assert db.executed == []


def test_list_reads_have_deterministic_tiebreaker():
    notes_db = _RecordingDB()
    UserNotesRepository(notes_db).get_all(1)
    assert "ORDER BY updated_at DESC, id DESC" in notes_db.fetched[0][0]

    searches_db = _RecordingDB()
    SavedSearchesRepository(searches_db).get_all(1)
    assert "ORDER BY created_at DESC, id DESC" in searches_db.fetched[0][0]
