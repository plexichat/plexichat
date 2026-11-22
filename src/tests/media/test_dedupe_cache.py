"""Bounded, invalidation-correct dedupe caching for DatabaseStorage.

The dedupe accelerator caches must never (a) alias two different contents,
(b) grow without bound, or (c) keep serving a path whose blob was deleted or
overwritten. These tests pin all three properties plus the performance win
that motivates the caches.
"""

import hashlib
import sqlite3

from src.core.media.storage import database as dbmod
from src.core.media.storage.database import (
    MAX_CHECKSUM_INDEX_ENTRIES,
    MAX_CHECKSUM_MEMO_BYTES,
    MAX_CHECKSUM_MEMO_ENTRIES,
    MAX_MEMO_PAYLOAD_BYTES,
    DatabaseStorage,
)


class MemoryDB:
    """Minimal DB adapter backed by in-memory SQLite, counting reads."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.fetch_one_calls = 0
        self.conn.execute(
            "CREATE TABLE media_blobs (path TEXT PRIMARY KEY, content BLOB NOT NULL, "
            "content_type TEXT NOT NULL, size INTEGER NOT NULL, checksum TEXT NOT NULL, "
            "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        self.conn.execute("CREATE INDEX idx ON media_blobs(checksum)")

    def convert_schema(self, schema):
        return schema

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def fetch_one(self, sql, params=()):
        self.fetch_one_calls += 1
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None


def make_storage():
    db = MemoryDB()
    return db, DatabaseStorage(db, max_size=10 * 1024 * 1024)


# ---------------------------------------------------------------------------
# correctness
# ---------------------------------------------------------------------------


def test_checksum_memo_is_content_sensitive_no_fingerprint_aliasing():
    """Two payloads with equal length and equal prefix/suffix must not alias."""
    _, storage = make_storage()
    # Same length, same first 32 and last 32 bytes; only the middle differs.
    a = b"A" * 32 + b"left " + b"Z" * 32
    b = b"A" * 32 + b"right" + b"Z" * 32
    assert len(a) == len(b)

    real = hashlib.sha256
    ca = storage._compute_checksum(a)
    cb = storage._compute_checksum(b)
    assert ca == real(a).hexdigest()
    assert cb == real(b).hexdigest()
    assert ca != cb
    # Both exact keys are retained independently in the memo.
    assert storage.dedupe_cache_stats()["memo_size"] == 2


def test_checksum_memo_reuses_identical_payload():
    _, storage = make_storage()
    payload = b"x" * 128
    real_sha256 = hashlib.sha256
    calls = {"n": 0}

    def counting(data):
        calls["n"] += 1
        return real_sha256(data)

    dbmod.hashlib.sha256 = counting
    try:
        first = storage._compute_checksum(payload)
        calls["n"] = 0
        for _ in range(10):
            assert storage._compute_checksum(payload) == first
        assert calls["n"] == 0
    finally:
        dbmod.hashlib.sha256 = real_sha256


def test_get_by_checksum_returns_correct_path_and_caches_it():
    db, storage = make_storage()
    payload = b"dedupe payload" * 20
    checksum = storage._compute_checksum(payload)
    assert storage.get_by_checksum(checksum) is None
    storage.store(payload, "image/a.png", "image/png")

    # A successful store pre-populates the index, so the first lookup is a hit.
    before = db.fetch_one_calls
    assert storage.get_by_checksum(checksum) == "image/a.png"
    assert db.fetch_one_calls == before
    for _ in range(5):
        assert storage.get_by_checksum(checksum) == "image/a.png"
    assert db.fetch_one_calls == before  # all hits are pure cache reads

    # With the index cleared, exactly one DB read repopulates it.
    storage._checksum_index.clear()
    before = db.fetch_one_calls
    assert storage.get_by_checksum(checksum) == "image/a.png"
    assert db.fetch_one_calls == before + 1


def test_get_by_checksum_distinguishes_different_content():
    db, storage = make_storage()
    a, b = b"content-A" * 20, b"content-B" * 20
    storage.store(a, "image/a.png", "image/png")
    storage.store(b, "image/b.png", "image/png")
    ca, cb = storage._compute_checksum(a), storage._compute_checksum(b)
    assert storage.get_by_checksum(ca) == "image/a.png"
    assert storage.get_by_checksum(cb) == "image/b.png"
    assert ca != cb


# ---------------------------------------------------------------------------
# invalidation
# ---------------------------------------------------------------------------


def test_delete_invalidates_checksum_index():
    db, storage = make_storage()
    payload = b"to delete" * 20
    checksum = storage._compute_checksum(payload)
    storage.store(payload, "image/gone.png", "image/png")
    assert storage.get_by_checksum(checksum) == "image/gone.png"

    before = db.fetch_one_calls
    assert storage.delete("image/gone.png") is True
    assert storage.get_by_checksum(checksum) is None
    assert (
        db.fetch_one_calls > before
    )  # re-queried the DB, not served from a stale cache


def test_overwrite_invalidates_old_checksum_mapping():
    db, storage = make_storage()
    old, new = b"old content" * 20, b"new content" * 20
    old_sum = storage._compute_checksum(old)
    new_sum = storage._compute_checksum(new)
    storage.store(old, "image/reused.png", "image/png")
    assert storage.get_by_checksum(old_sum) == "image/reused.png"

    storage.store(new, "image/reused.png", "image/png")
    # The old checksum must no longer resolve to the overwritten path.
    assert storage.get_by_checksum(old_sum) is None
    assert storage.get_by_checksum(new_sum) == "image/reused.png"


def test_cleanup_orphaned_clears_index():
    db, storage = make_storage()
    payload = b"orphan" * 20
    checksum = storage._compute_checksum(payload)
    storage.store(payload, "image/keep.png", "image/png")
    storage.store(payload + b"x", "image/drop.png", "image/png")
    storage.get_by_checksum(checksum)
    assert storage.dedupe_cache_stats()["index_size"] >= 1

    storage.cleanup_orphaned(["image/keep.png"])
    assert storage.dedupe_cache_stats()["index_size"] == 0
    # The surviving blob is still discoverable after a fresh DB lookup.
    assert (
        storage.get_by_checksum(storage._compute_checksum(payload)) == "image/keep.png"
    )


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------


def test_checksum_index_is_bounded():
    _, storage = make_storage()
    for i in range(MAX_CHECKSUM_INDEX_ENTRIES + 75):
        payload = f"unique-{i}".encode()
        storage.store(payload, f"doc/{i}.txt", "text/plain")
        storage.get_by_checksum(storage._compute_checksum(payload))
    stats = storage.dedupe_cache_stats()
    assert stats["index_size"] <= MAX_CHECKSUM_INDEX_ENTRIES


def test_checksum_memo_is_bounded_by_count_and_bytes():
    _, storage = make_storage()
    n = MAX_CHECKSUM_MEMO_ENTRIES + 40
    for i in range(n):
        storage._compute_checksum(f"memo-key-{i}".encode() * 4)
    stats = storage.dedupe_cache_stats()
    assert stats["memo_size"] <= MAX_CHECKSUM_MEMO_ENTRIES
    assert stats["memo_bytes"] <= MAX_CHECKSUM_MEMO_BYTES


def test_oversized_payload_is_not_memoized():
    _, storage = make_storage()
    big = b"z" * MAX_MEMO_PAYLOAD_BYTES
    checksum = storage._compute_checksum(big)
    assert checksum == hashlib.sha256(big).hexdigest()
    assert storage.dedupe_cache_stats()["memo_size"] == 0
