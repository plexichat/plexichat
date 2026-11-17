"""Regression: streamed chunk writes must confine the DB-sourced temp path.

``upload_chunk_stream`` used to open ``session.temp_path`` verbatim while every
other consumer of that DB column (``upload_chunk``, ``complete_session``,
``_cleanup_session``, ``cleanup_expired``) runs it through
``_confine_temp_path``. A tampered/forged session row could therefore make the
streaming writer seek and write attacker-controlled bytes into any file the
process can open.
"""

import io
import os
import time

import pytest

from src.core.media.chunked import (
    ChunkedUploadManager,
    UploadSession,
    UploadSessionStatus,
)


class _DB:
    def fetch_one(self, *args, **kwargs):
        return None

    def fetch_all(self, *args, **kwargs):
        return []

    def execute(self, *args, **kwargs):
        return None


@pytest.fixture
def manager():
    return ChunkedUploadManager(_DB())


def _session(temp_path):
    return UploadSession(
        id="sess",
        user_id=1,
        filename="chunk.bin",
        content_type="application/octet-stream",
        total_size=100,
        chunk_size=100,
        total_chunks=1,
        uploaded_chunks=0,
        uploaded_bytes=0,
        status=UploadSessionStatus.IN_PROGRESS,
        created_at=0,
        updated_at=0,
        expires_at=int(time.time() * 1000) + 60_000,
        temp_path=temp_path,
    )


def test_stream_chunk_refuses_temp_path_outside_managed_dir(manager, tmp_path):
    victim = tmp_path / "victim.tmp"
    victim.write_bytes(b"ORIGINAL")
    session = _session(str(victim))
    manager.get_session = lambda sid, uid: session

    result = manager.upload_chunk_stream("sess", 1, 0, io.BytesIO(b"PWNED!!"))

    assert result.success is False
    assert "unsafe" in (result.error or "").lower()
    assert victim.read_bytes() == b"ORIGINAL"


def test_stream_chunk_refuses_missing_temp_path(manager):
    session = _session(None)
    manager.get_session = lambda sid, uid: session

    result = manager.upload_chunk_stream("sess", 1, 0, io.BytesIO(b"data"))

    assert result.success is False
    assert "unsafe" in (result.error or "").lower()


def test_stream_chunk_still_accepts_confined_temp_path(manager):
    path = os.path.join(manager._temp_dir, "legit-stream.tmp")
    with open(path, "wb") as handle:
        handle.write(b"\x00" * 100)
    try:
        session = _session(path)
        manager.get_session = lambda sid, uid: session
        manager._record_chunk_progress = lambda sid, idx, length, now: (1, length, True)

        result = manager.upload_chunk_stream("sess", 1, 0, io.BytesIO(b"OK"))

        assert result.success is True
        with open(path, "rb") as handle:
            assert handle.read(2) == b"OK"
    finally:
        if os.path.exists(path):
            os.unlink(path)
