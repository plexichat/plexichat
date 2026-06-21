"""Media upload/download round-trip coverage."""

from __future__ import annotations

import pytest

from src.core.media import setup, get_file, get_file_data, delete_file


pytestmark = pytest.mark.integration


@pytest.fixture
def media_setup(db):
    setup(db)
    return db


class TestMediaUpload:
    def test_upload_small_image(self, media_setup, test_user):
        result = setup.upload_file(
            user_id=test_user.id,
            file_data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
            filename="pixel.png",
            content_type="image/png",
        )
        assert result.file_id > 0
        assert result.filename == "pixel.png"

    def test_get_file_by_id(self, media_setup, test_user):
        result = setup.upload_file(
            user_id=test_user.id,
            file_data=b"hello world" * 32,
            filename="blob.bin",
            content_type="application/octet-stream",
        )
        fetched = get_file(result.file_id)
        assert fetched is not None
        assert fetched.id == result.file_id

    def test_get_file_data_round_trip(self, media_setup, test_user):
        payload = b"round-trip bytes " + b"\x42" * 40
        result = setup.upload_file(
            user_id=test_user.id,
            file_data=payload,
            filename="rt.bin",
            content_type="application/octet-stream",
        )
        data, content_type = get_file_data(result.file_id)
        assert data == payload
        assert content_type == "application/octet-stream"

    def test_delete_file(self, media_setup, test_user):
        result = setup.upload_file(
            user_id=test_user.id,
            file_data=b"delete me please",
            filename="rm.bin",
            content_type="application/octet-stream",
        )
        assert delete_file(test_user.id, result.file_id) is True
