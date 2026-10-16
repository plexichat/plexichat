"""ChunkedUploadManager end-to-end smoke tests."""

from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def chunked(db):
    from src.core.media.chunked import ChunkedUploadManager, create_tables

    create_tables(db)
    return ChunkedUploadManager(db)


class TestChunkedSessions:
    def test_create_session(self, chunked, test_user):
        session = chunked.create_session(
            user_id=test_user.id,
            filename="big.bin",
            content_type="application/octet-stream",
            total_size=10 * 1024 * 1024,
        )
        assert session is not None
        assert session.total_chunks > 0
        assert session.status.value == "pending"

    def test_upload_single_chunk(self, chunked, test_user):
        session = chunked.create_session(
            user_id=test_user.id,
            filename="one.bin",
            content_type="application/octet-stream",
            total_size=chunked._config["chunk_size"],
        )
        result = chunked.upload_chunk(
            session.id,
            test_user.id,
            0,
            b"X" * chunked._config["chunk_size"],
        )
        assert result.success is True
        assert result.uploaded_chunks == 1
        assert result.is_complete is True

    def test_cancel_session(self, chunked, test_user):
        session = chunked.create_session(
            user_id=test_user.id,
            filename="cancel.bin",
            content_type="application/octet-stream",
            total_size=chunked._config["chunk_size"],
        )
        assert chunked.cancel_session(session.id, test_user.id) is True

    def test_completed_session_returns_bytes(self, chunked, test_user):
        chunk_size = chunked._config["chunk_size"]
        session = chunked.create_session(
            user_id=test_user.id,
            filename="small.bin",
            content_type="application/octet-stream",
            total_size=chunk_size,
        )
        chunk = os.urandom(chunk_size)
        chunked.upload_chunk(session.id, test_user.id, 0, chunk)
        # Don't exceed the in-memory cap; small payload is safe.
        if session.total_size <= chunked._MAX_COMPLETE_SESSION_BYTES:
            file_data = chunked.complete_session(session.id, test_user.id)
            assert file_data == chunk

    def test_user_sessions_list(self, chunked, test_user):
        chunked.create_session(
            user_id=test_user.id,
            filename="list.bin",
            content_type="application/octet-stream",
            total_size=1024,
        )
        assert any(
            s.filename == "list.bin" for s in chunked.get_user_sessions(test_user.id)
        )

    def test_signature_mismatch(self, chunked, test_user):
        import hashlib

        chunk = b"Y" * 1024
        session = chunked.create_session(
            user_id=test_user.id,
            filename="bad.bin",
            content_type="application/octet-stream",
            total_size=len(chunk),
        )
        result = chunked.upload_chunk(
            session.id,
            test_user.id,
            0,
            b"Z" * len(chunk),
            chunk_checksum=hashlib.sha256(chunk).hexdigest(),
        )
        assert result.success is False
        assert "checksum" in (result.error or "").lower()

    def test_cleanup_expired(self, chunked, test_user):
        # No expired sessions; the helper must simply return 0.
        assert chunked.cleanup_expired() == 0

    def test_duplicate_chunk_index_does_not_complete(self, chunked, test_user):
        # M7 fix: resending the same chunk index repeatedly must NOT mark the
        # session complete (that would leave the rest of the file as
        # pre-allocated zero bytes).
        # NOTE: chunked sessions are now validated against the same per-type
        # size limits as direct uploads (video: 100MB default), so use a type
        # whose limit accommodates a multi-chunk payload.
        chunk_size = chunked._config["chunk_size"]
        session = chunked.create_session(
            user_id=test_user.id,
            filename="dup.bin",
            content_type="video/mp4",
            total_size=3 * chunk_size,
        )
        last = None
        for _ in range(3):
            last = chunked.upload_chunk(session.id, test_user.id, 0, b"A" * chunk_size)
        assert last.success is True
        # Only one distinct chunk was received.
        assert last.uploaded_chunks == 1
        assert last.is_complete is False

    def test_all_distinct_chunks_complete(self, chunked, test_user):
        chunk_size = chunked._config["chunk_size"]
        n = 3
        session = chunked.create_session(
            user_id=test_user.id,
            filename="all.bin",
            content_type="video/mp4",
            total_size=n * chunk_size,
        )
        last = None
        for i in range(n):
            last = chunked.upload_chunk(
                session.id, test_user.id, i, bytes([65 + i]) * chunk_size
            )
        assert last.uploaded_chunks == n
        assert last.is_complete is True


class _TupleSessionDB:
    """A ``fetch_all`` that returns bare tuples, as a raw cursor does.

    The documented row shapes are "mapping or tuple" (see
    docs/admin-hardening.md), and ``get_session`` in chunked.py handles both.
    """

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def fetch_all(self, query, params=()):
        self.calls.append((query, params))
        return list(self._rows)


class TestChunkedSessionRowShapes:
    """get_user_sessions must read positional rows, not just mapping rows."""

    # The columns get_user_sessions selects, in the order it selects them.
    _ROW = (
        "sess-tuple-1",  # id
        7,  # user_id
        "list.bin",  # filename
        "application/octet-stream",  # content_type
        1024,  # total_size
        5 * 1024 * 1024,  # chunk_size
        1,  # total_chunks
        0,  # uploaded_chunks
        0,  # uploaded_bytes
        "pending",  # status
        1,  # created_at
        2,  # updated_at
        9_999_999_999_999,  # expires_at (future)
        "/tmp/list.bin.tmp",  # temp_path
        None,  # checksum
    )

    def test_get_user_sessions_maps_tuple_rows(self, db):
        # ``db`` is requested (and unused) to initialise config exactly as the
        # neighbouring tests do; the manager is built on the tuple-row stub so
        # the assertion is independent of the configured driver.
        from src.core.media.chunked import ChunkedUploadManager

        manager = ChunkedUploadManager(_TupleSessionDB([self._ROW]))

        sessions = manager.get_user_sessions(7)

        assert [s.id for s in sessions] == ["sess-tuple-1"]
        assert sessions[0].filename == "list.bin"
        assert sessions[0].total_size == 1024
        assert sessions[0].status.value == "pending"
