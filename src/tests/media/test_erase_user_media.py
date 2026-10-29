"""Regression: erasing a user's media must not defer for ever on dedup counts.

``delete_file_permanently`` refuses while a file's checksum is referenced more
than once. A user who uploaded the same content twice therefore held a
reference count of two against their own files, every call was refused, and the
account purge left the rows -- and the objects -- behind. ``delete_files_for_user``
releases the dedup counter once per row and removes the object only when no
other user's file shares it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class FakeStorage:
    def __init__(self, present=True, delete_result=True):
        self.deleted = []
        self._present = present
        self._delete_result = delete_result

    def exists(self, path):
        return self._present

    def delete(self, path):
        self.deleted.append(path)
        return self._delete_result


def _insert_file(db, *, user_id, file_id, checksum, path, filename):
    db.execute(
        "INSERT INTO media_files "
        "(id, filename, original_filename, content_type, size, media_type, "
        " storage_backend, storage_path, checksum, uploaded_by, uploaded_at) "
        "VALUES (?, ?, ?, 'image/png', 44, 'image', 's3', ?, ?, ?, ?)",
        (file_id, filename, filename, path, checksum, int(user_id), 1_700_000_000_000),
    )


def _insert_hash(db, *, hash_id, checksum, count, path="image/shared.png"):
    db.execute(
        "INSERT INTO media_file_hashes "
        "(id, hash_value, algorithm, file_size, content_type, reference_count, "
        " first_seen, storage_path, storage_backend) "
        "VALUES (?, ?, 'sha256', 44, 'image/png', ?, ?, ?, 's3')",
        (hash_id, checksum, count, 1_700_000_000_000, path),
    )


@pytest.fixture
def storage(media_manager, monkeypatch):
    backend = FakeStorage()
    monkeypatch.setattr(media_manager, "_get_storage_by_backend", lambda name: backend)
    return backend


def test_duplicate_uploads_of_one_user_are_all_erased(media_manager, db, storage):
    user_id = 900001
    checksum = "a" * 64
    _insert_hash(db, hash_id=1, checksum=checksum, count=2, path="image/dup.png")
    _insert_file(
        db,
        user_id=user_id,
        file_id=11,
        checksum=checksum,
        path="image/dup.png",
        filename="one.png",
    )
    _insert_file(
        db,
        user_id=user_id,
        file_id=12,
        checksum=checksum,
        path="image/dup.png",
        filename="two.png",
    )

    unswept = media_manager.delete_files_for_user(user_id)

    assert unswept == []
    assert (
        db.fetch_all("SELECT id FROM media_files WHERE uploaded_by = ?", (user_id,))
        == []
    )
    # Two reference decrements release the hash row.
    assert (
        db.fetch_one(
            "SELECT id FROM media_file_hashes WHERE hash_value = ?", (checksum,)
        )
        is None
    )
    # The object is removed once, not once per file row.
    assert storage.deleted == ["image/dup.png"]


def test_content_shared_with_another_user_keeps_the_object(media_manager, db, storage):
    user_id, other_id = 910001, 910002
    checksum = "b" * 64
    _insert_hash(db, hash_id=2, checksum=checksum, count=2, path="image/shared.png")
    _insert_file(
        db,
        user_id=user_id,
        file_id=21,
        checksum=checksum,
        path="image/shared.png",
        filename="mine.png",
    )
    _insert_file(
        db,
        user_id=other_id,
        file_id=22,
        checksum=checksum,
        path="image/shared.png",
        filename="theirs.png",
    )

    unswept = media_manager.delete_files_for_user(user_id)

    assert unswept == []
    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (21,)) is None
    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (22,)) is not None, (
        "another user's upload must survive"
    )
    assert storage.deleted == [], "the shared object is still in use"
    row = db.fetch_one(
        "SELECT reference_count FROM media_file_hashes WHERE hash_value = ?",
        (checksum,),
    )
    assert int(row["reference_count"]) == 1


def test_object_that_cannot_be_deleted_is_reported(media_manager, db, monkeypatch):
    user_id = 920001
    checksum = "c" * 64
    _insert_hash(db, hash_id=3, checksum=checksum, count=1, path="image/stuck.png")
    _insert_file(
        db,
        user_id=user_id,
        file_id=31,
        checksum=checksum,
        path="image/stuck.png",
        filename="stuck.png",
    )
    backend = FakeStorage(delete_result=False)
    monkeypatch.setattr(media_manager, "_get_storage_by_backend", lambda name: backend)

    unswept = media_manager.delete_files_for_user(user_id)

    assert unswept == ["image/stuck.png"]
    # The metadata still goes: reporting the object is the operator's problem.
    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (31,)) is None
