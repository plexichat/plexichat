"""Regression: erasing media must batch the shared-checksum probe.

``delete_files_for_user`` used to issue one
``SELECT COUNT(*) ... WHERE checksum = ? AND uploaded_by != ?`` per distinct
checksum owned by the user. A user with thousands of deduplicated uploads
therefore paid thousands of round trips during an account purge (a classic
N+1), and the per-checksum statement could not be bounded as a single query.

The fix resolves all checksums of the erasing user in bounded ``IN`` chunks.
These tests pin both the query-count/bind-count bounds and the safety contract:
the batched read is scoped to the erasing user's own checksums and is used only
to decide whether that user's objects may be deleted -- another user's file
metadata and rows are never exposed or removed.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.integration


USER_ID = 940001
OTHER_USER_ID = 940002
CHUNK = 400


class _RecordingDB:
    """Delegate to the real DB while recording every emitted statement."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def fetch_one(self, sql, params=None):
        self.calls.append(("fetch_one", sql, tuple(params or ())))
        return self._real.fetch_one(sql, params)

    def fetch_all(self, sql, params=None):
        self.calls.append(("fetch_all", sql, tuple(params or ())))
        return self._real.fetch_all(sql, params)

    def execute(self, sql, params=None, **kwargs):
        self.calls.append(("execute", sql, tuple(params or ())))
        return self._real.execute(sql, params, **kwargs)


def _insert_file(db, *, user_id, file_id, checksum, path):
    db.execute(
        "INSERT INTO media_files "
        "(id, filename, original_filename, content_type, size, media_type, "
        " storage_backend, storage_path, checksum, uploaded_by, uploaded_at) "
        "VALUES (?, ?, ?, 'image/png', 44, 'image', 's3', ?, ?, ?, ?)",
        (
            file_id,
            f"f{file_id}.png",
            f"f{file_id}.png",
            path,
            checksum,
            user_id,
            1_700_000_000_000,
        ),
    )


def _insert_hash(db, *, hash_id, checksum, count, path):
    db.execute(
        "INSERT INTO media_file_hashes "
        "(id, hash_value, algorithm, file_size, content_type, reference_count, "
        " first_seen, storage_path, storage_backend) "
        "VALUES (?, ?, 'sha256', 44, 'image/png', ?, ?, ?, 's3')",
        (hash_id, checksum, count, 1_700_000_000_000, path),
    )


@pytest.fixture
def storage(media_manager, monkeypatch):
    class FakeStorage:
        def __init__(self):
            self.deleted = []

        def exists(self, path):
            return True

        def delete(self, path):
            self.deleted.append(path)
            return True

    backend = FakeStorage()
    monkeypatch.setattr(media_manager, "_get_storage_by_backend", lambda name: backend)
    return backend


def _spy(media_manager):
    real = media_manager._db
    spy = _RecordingDB(real)
    media_manager._db = spy
    return real, spy


def test_shared_checksum_probe_is_batched_and_chunked(media_manager, db, storage):
    # Span three chunks so chunking -- not just batching -- is exercised.
    n = CHUNK * 2 + 50
    for i in range(n):
        checksum = f"{(i + 1):064x}"
        path = f"image/user/{i}.png"
        _insert_file(
            db, user_id=USER_ID, file_id=10_000 + i, checksum=checksum, path=path
        )
        _insert_hash(db, hash_id=10_000 + i, checksum=checksum, count=1, path=path)

    real, spy = _spy(media_manager)
    try:
        unswept = media_manager.delete_files_for_user(USER_ID)
    finally:
        media_manager._db = real

    assert unswept == []
    probes = [c for c in spy.calls if "uploaded_by != ?" in c[1]]
    # One query per bounded chunk, never one per checksum.
    assert len(probes) == math.ceil(n / CHUNK)
    for _kind, sql, params in probes:
        assert sql.count("?") <= CHUNK + 1
        assert len(params) <= CHUNK + 1
        assert params[0] == USER_ID, "the probe must be scoped to the erasing user"
    assert len(storage.deleted) == n
    assert (
        db.fetch_all("SELECT id FROM media_files WHERE uploaded_by = ?", (USER_ID,))
        == []
    )


def test_shared_object_and_other_users_row_survive(media_manager, db, storage):
    checksum = "f" * 64
    path = "image/shared.png"
    _insert_hash(db, hash_id=99_000, checksum=checksum, count=2, path=path)
    _insert_file(db, user_id=USER_ID, file_id=99_001, checksum=checksum, path=path)
    _insert_file(
        db, user_id=OTHER_USER_ID, file_id=99_002, checksum=checksum, path=path
    )

    real, spy = _spy(media_manager)
    try:
        unswept = media_manager.delete_files_for_user(USER_ID)
    finally:
        media_manager._db = real

    assert unswept == []
    assert storage.deleted == [], "a checksum shared with another user keeps the object"
    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (99_001,)) is None
    assert (
        db.fetch_one("SELECT id FROM media_files WHERE id = ?", (99_002,)) is not None
    ), "another user's metadata must survive"
    # The batched probe is scoped by the erasing user's id and only SELECTs a
    # checksum -- never another user's filename/path/id.
    probes = [c for c in spy.calls if "uploaded_by != ?" in c[1]]
    assert len(probes) == 1
    assert "SELECT DISTINCT checksum" in probes[0][1]
    assert "storage_path" not in probes[0][1]
    assert probes[0][2][0] == USER_ID
