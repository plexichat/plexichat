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
            total_size=len(chunk) * 2,
        )
        chunked.upload_chunk(session.id, test_user.id, 0, chunk)
        # Now try uploading a second chunk with a wrong checksum
        result = chunked.upload_chunk(
            session.id,
            test_user.id,
            1,
            b"Z" * len(chunk),
            chunk_checksum=hashlib.sha256(chunk).hexdigest(),
        )
        assert result.success is False
        assert "checksum" in (result.error or "").lower()

    def test_cleanup_expired(self, chunked, test_user):
        # No expired sessions; the helper must simply return 0.
        assert chunked.cleanup_expired() == 0
