"""Test protection against Content-Type spoofing."""
import pytest
import io
from PIL import Image
from src.core.media.models import MediaType

@pytest.mark.media
class TestContentTypeSpoofing:
    def test_image_spoofed_as_binary_is_still_processed(self, media_module, user_pool, sample_image_bytes):
        """Test that an image uploaded as application/octet-stream is detected and processed."""
        user = user_pool.get_user()
        
        # Upload valid image but claim it's generic binary data
        # Use .jpg extension to avoid extension-based blocking
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="spoofed.jpg",
            content_type="application/octet-stream"
        )
        
        # Verify it was detected as an image
        assert result.content_type == "image/jpeg"
        
        stored_file = media_module.get_file(result.file_id)
        assert stored_file.media_type == MediaType.IMAGE
        # Check that metadata was extracted (proving ImageProcessor ran)
        assert stored_file.metadata is not None
        assert "width" in stored_file.metadata

    def test_decompression_bomb_spoofed_as_text_is_rejected(self, media_module, user_pool):
        """Test that a decompression bomb claimed as text is still caught."""
        pytest.importorskip("PIL")
        from PIL import Image
        
        user = user_pool.get_user()
        
        # Create a small-on-disk but huge-in-pixels image
        huge_img = Image.new("RGB", (20000, 20000), color="red")
        buffer = io.BytesIO()
        huge_img.save(buffer, format="JPEG", quality=1)
        bomb_data = buffer.getvalue()
        
        # Upload claiming it's plain text
        with pytest.raises(Exception) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=bomb_data,
                filename="not_a_bomb.txt",
                content_type="text/plain"
            )
        
        error_msg = str(exc_info.value).lower()
        # It should be caught by ImageProcessor because it was detected as image/jpeg
        assert any(word in error_msg for word in ["pixel", "dimension", "limit"])
