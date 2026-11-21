"""Storage file listing must resolve per-file metadata in one batched query.

A naive listing fetches the page of rows and then issues a follow-up
``fetch_one`` per file to enrich it with ``content_type`` / ``checksum`` /
``scan_status`` metadata. For a page of N files that is N+1 database reads and
grows linearly with the page size. ``StorageQuotaManager.list_user_files`` now
selects the metadata columns in the same statement as the page, so the whole
listing is a single query independent of N.

The test drives the real manager and measures the reduction by comparing the
batched path against a local reimplementation of the old per-file approach.
Both paths are fed the same rows and must return byte-for-byte identical data.
"""

from __future__ import annotations

import time

import pytest

from src.core.storage_quota.manager import StorageQuotaManager

pytestmark = [pytest.mark.unit]

_USER_ID = 42
_PAGE_SIZE = 16

# Columns the listing adds to the row beyond the original page projection.
_METADATA_COLUMNS = (
    "content_type",
    "media_type",
    "storage_backend",
    "checksum",
    "scan_status",
    "scan_result",
)


class _CountingDB:
    """Delegate to the real DB while counting reads and capturing SQL."""

    def __init__(self, db):
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "reads", [])
        object.__setattr__(self, "sql", [])

    def __getattr__(self, name):
        attr = getattr(self.db, name)

        def wrapped(*args, **kwargs):
            if name in ("fetch_all", "fetch_one"):
                object.__setattr__(
                    self, "reads", self.reads + [(name, args[0] if args else "")]
                )
                object.__setattr__(self, "sql", self.sql + [args[0] if args else ""])
            return attr(*args, **kwargs)

        return wrapped


def _seed(db, count: int) -> None:
    db.execute("DELETE FROM media_files")
    now = int(time.time() * 1000)
    for i in range(count):
        db.execute(
            "INSERT INTO media_files (id, uploaded_by, filename, original_filename, "
            "content_type, size, media_type, storage_backend, storage_path, "
            "checksum, uploaded_at, scan_status, scan_result, deleted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                500_000 + i,
                _USER_ID if i % 2 == 0 else 99,
                f"f{i}.bin",
                f"f{i}.bin",
                "image/png" if i % 2 == 0 else "text/plain",
                100 + i,
                "image" if i % 2 == 0 else "document",
                "local",
                f"uploads/{_USER_ID}/f{i}.bin",
                f"checksum-{i}",
                now + i,
                "clean" if i % 2 == 0 else "pending",
                None,
            ),
        )


def _naive_list_with_metadata(db, user_id: int, limit: int, offset: int) -> dict:
    """Pre-optimization behaviour: page rows + one metadata read per file."""
    rows = db.fetch_all(
        "SELECT id, filename, original_filename, size, storage_path, uploaded_at "
        "FROM media_files WHERE uploaded_by = ? AND deleted = 0 "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )
    files = []
    for row in rows:
        item = dict(row)
        meta = db.fetch_one(
            "SELECT content_type, media_type, storage_backend, checksum, "
            "scan_status, scan_result FROM media_files WHERE id = ?",
            (item["id"],),
        )
        item.update({col: meta[col] for col in _METADATA_COLUMNS})
        item["id"] = str(item["id"])
        files.append(item)
    return {"files": files, "limit": limit, "offset": offset}


def test_storage_listing_batches_per_file_metadata(db):
    _seed(db, _PAGE_SIZE * 2)

    counting = _CountingDB(db)
    manager = StorageQuotaManager(counting)
    batched = manager.list_user_files(_USER_ID, limit=_PAGE_SIZE, offset=0)
    batched_reads = list(counting.reads)
    batched_sql = list(counting.sql)

    naive_db = _CountingDB(db)
    naive = _naive_list_with_metadata(naive_db, _USER_ID, _PAGE_SIZE, 0)
    naive_reads = list(naive_db.reads)

    # Both paths return the same page with the same metadata.
    assert batched == naive
    assert len(batched["files"]) == _PAGE_SIZE
    for item in batched["files"]:
        assert isinstance(item["id"], str)
        for col in _METADATA_COLUMNS:
            assert col in item
        assert item["content_type"] in ("image/png", "text/plain")
        assert item["scan_status"] in ("clean", "pending")

    # Before: one page read plus one metadata read per returned file.
    assert len(naive_reads) == _PAGE_SIZE + 1
    # After: exactly one read, whatever the page size.
    assert len(batched_reads) == 1
    assert batched_reads[0][0] == "fetch_all"
    assert "content_type" in batched_sql[0]
    assert "scan_status" in batched_sql[0]
    assert " IN (" not in batched_sql[0]  # no per-id IN fan-out either
    assert len(batched_reads) < len(naive_reads)


def test_storage_listing_query_count_is_independent_of_page_size(db):
    """The batched listing issues one query for a 1-row and a 200-row page."""
    # Half the rows belong to the target user, so seed enough to fill a
    # 200-row page for them.
    _seed(db, 500)

    for limit in (1, 50, 200):
        counting = _CountingDB(db)
        result = StorageQuotaManager(counting).list_user_files(
            _USER_ID, limit=limit, offset=0
        )
        assert len(result["files"]) == limit
        assert len(counting.reads) == 1, (
            f"page size {limit} issued {len(counting.reads)} reads: {counting.reads}"
        )
