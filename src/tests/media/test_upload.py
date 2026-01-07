"""
Tests for file upload functionality.
"""

import pytest
import asyncio
import io
import uuid


@pytest.mark.asyncio
@pytest.mark.media
class TestMediaAsync:
    """Enhanced asynchronous media tests."""

    async def test_concurrent_uploads(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test multiple concurrent file uploads to verify storage thread safety and ID generation."""
        user = user_pool.get_user()

        # Upload 10 images in parallel
        tasks = [
            asyncio.to_thread(
                media_module.upload_file,
                user_id=user.id,
                file_data=sample_image_bytes,
                filename=f"async_test_{i}.jpg",
                content_type="image/jpeg",
            )
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        # All IDs should be unique
        assert len({r.file_id for r in results}) == 10

    async def test_magic_byte_validation(self, media_module, user_pool):
        """Test that the system correctly validates magic bytes regardless of the claimed MIME type."""
        user = user_pool.get_user()

        # Claim it's an image but send plain text
        fake_image_data = b"This is clearly not a JPEG image file content"

        with pytest.raises(Exception) as exc_info:
            await asyncio.to_thread(
                media_module.upload_file,
                user_id=user.id,
                file_data=fake_image_data,
                filename="scam.jpg",
                content_type="image/jpeg",
            )

        assert (
            "declared type" in str(exc_info.value)
            or "signature" in str(exc_info.value).lower()
        )

    async def test_rate_limit_enforcement(
        self, media_module, modules, sample_text_bytes, monkeypatch
    ):
        """Test that upload rate limits are strictly enforced across concurrent requests."""
        # Explicitly set limit to 10 per minute
        monkeypatch.setitem(
            media_module._get_manager()._config["rate_limit"], "uploads_per_minute", 10
        )
        monkeypatch.setitem(
            media_module._get_manager()._config["rate_limit"], "enabled", True
        )

        # Use fresh user for isolation
        unique_id = uuid.uuid4().hex[:8]
        user = await asyncio.to_thread(
            modules.auth.register,
            username=f"async_rate_{unique_id}",
            email=f"async_rate_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Default limit is 10 per minute in our manager logic
        # We'll try to upload 15 small text files
        tasks = [
            asyncio.to_thread(
                media_module.upload_file,
                user_id=user.id,
                file_data=sample_text_bytes,
                filename=f"limit_test_{i}.txt",
                content_type="text/plain",
            )
            for i in range(15)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) <= 10
        assert any("Rate limit" in str(f) for f in failures)

    async def test_thumbnail_regeneration_on_demand(
        self, media_module, user_pool, sample_image_bytes
    ):
        """Test creating additional thumbnails after the initial upload."""
        user = user_pool.get_user()

        result = await asyncio.to_thread(
            media_module.upload_file,
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="thumb_demand.jpg",
        )

        # Create a non-standard size thumbnail
        thumb_url = await asyncio.to_thread(
            media_module.create_thumbnail,
            file_id=result.file_id,
            size=100,
            user_id=user.id,
        )

        assert thumb_url is not None
        assert "100" in thumb_url

    async def test_large_file_simulated_chunked_upload(self, media_module, user_pool):
        """Test the logic for handling larger files via simulated stream uploads."""
        user = user_pool.get_user()

        # Create 1MB of random data
        large_data = b"0" * (1024 * 1024)
        stream = io.BytesIO(large_data)

        result = await asyncio.to_thread(
            media_module.upload_stream,
            user_id=user.id,
            stream=stream,
            filename="large_file.bin",
            content_type="application/octet-stream",
            size=len(large_data),
        )

        assert result.size == len(large_data)

        # Verify we can retrieve it
        retrieved_data, _ = await asyncio.to_thread(
            media_module.get_file_data, result.file_id
        )
        assert len(retrieved_data) == len(large_data)
