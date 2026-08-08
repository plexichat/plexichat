"""Media upload/download round-trip coverage."""

from __future__ import annotations

import pytest

from src.core.media import setup, get_file, get_file_data, delete_file, upload_file


pytestmark = pytest.mark.integration


@pytest.fixture
def media_setup(db):
    from src.core import media

    media.setup(db)
    yield db
    # Wait for fire-and-forget background tasks before the db fixture
    # removes the temp SQLite file (see MediaManager.close docstring:
    # a thread touching a deleted mmap'd DB file crashes with SIGBUS).
    if media._manager is not None:
        try:
            media._manager.close()
        except Exception:
            pass
        media._manager = None
        media._setup_complete = False


class TestMediaUpload:
    def test_upload_small_image(self, media_setup, test_user):
        mini_png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        result = upload_file(
            user_id=test_user.id,
            file_data=mini_png,
            filename="pixel.png",
            content_type="image/png",
        )
        assert result.file_id > 0
        assert result.filename == "pixel.png"

    def test_get_file_by_id(self, media_setup, test_user):
        result = upload_file(
            user_id=test_user.id,
            file_data=b"hello world" * 32,
            filename="blob.dat",
            content_type="application/octet-stream",
        )
        fetched = get_file(result.file_id)
        assert fetched is not None
        assert fetched.id == result.file_id

    def test_get_file_data_round_trip(self, media_setup, test_user):
        payload = b"round-trip bytes " + b"\x42" * 40
        result = upload_file(
            user_id=test_user.id,
            file_data=payload,
            filename="rt.dat",
            content_type="application/octet-stream",
        )
        data, content_type = get_file_data(result.file_id)
        assert data == payload
        assert content_type == "application/octet-stream"

    def test_delete_file(self, media_setup, test_user):
        result = upload_file(
            user_id=test_user.id,
            file_data=b"delete me please",
            filename="rm.dat",
            content_type="application/octet-stream",
        )
        assert delete_file(test_user.id, result.file_id) is True
