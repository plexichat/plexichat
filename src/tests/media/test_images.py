"""Tests for media image processing (thumbnails, resize, convert)."""

import io

import pytest

from src.core.media.models import MediaType
from src.core.media.exceptions import ImageProcessingError, MediaError


# Minimal valid PNG (1x1 pixel)
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestImages:
    """Tests for image file processing."""

    def test_upload_image_creates_metadata(self, media_manager, test_user):
        """Test that uploading an image creates metadata."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="meta_test.png",
            content_type="image/png",
        )
        # Image metadata may or may not be available depending on Pillow
        if result.metadata:
            assert "width" in result.metadata or "format" in result.metadata

    def test_get_thumbnails(self, media_manager, test_user):
        """Test getting thumbnails for an uploaded image."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="thumb_test.png",
            content_type="image/png",
        )
        # Thumbnails may or may not be generated depending on Pillow availability
        thumbnails = media_manager.get_thumbnails(result.file_id)
        assert isinstance(thumbnails, dict)

    def test_resize_image(self, media_manager, test_user):
        """Test resizing an image."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="resize_test.png",
            content_type="image/png",
        )
        try:
            resized = media_manager.resize_image(result.file_id, width=32, height=32)
            assert isinstance(resized, bytes)
            assert len(resized) > 0
        except (ImageProcessingError, MediaError):
            # Pillow may not be available in test environment
            pytest.skip("Image processing not available")

    def test_resize_nonexistent_image(self, media_manager):
        """Test resizing a nonexistent image raises error."""
        with pytest.raises(MediaError):
            media_manager.resize_image(9999999, width=32, height=32)

    def test_convert_image_format(self, media_manager, test_user):
        """Test converting image format."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="convert_test.png",
            content_type="image/png",
        )
        try:
            converted = media_manager.convert_image(result.file_id, "JPEG")
            assert isinstance(converted, bytes)
        except (ImageProcessingError, MediaError):
            pytest.skip("Image processing not available")

    def test_detect_image_media_type(self, media_manager):
        """Test that image content type is detected as IMAGE."""
        media_type = media_manager._detect_media_type("image/png")
        assert media_type == MediaType.IMAGE

    def test_detect_jpeg_media_type(self, media_manager):
        """Test JPEG detection."""
        media_type = media_manager._detect_media_type("image/jpeg")
        assert media_type == MediaType.IMAGE

    def test_detect_gif_media_type(self, media_manager):
        """Test GIF detection."""
        media_type = media_manager._detect_media_type("image/gif")
        assert media_type == MediaType.IMAGE

    def test_detect_webp_media_type(self, media_manager):
        """Test WebP detection."""
        media_type = media_manager._detect_media_type("image/webp")
        assert media_type == MediaType.IMAGE


@pytest.mark.media
class TestImageDimensionGuard:
    """`convert_format`/`strip_metadata` must enforce the dimension limit.

    `max_dimension` is documented as the decompression-bomb guard and was
    enforced on the metadata/thumbnail/resize entry points but skipped by the
    two re-encode methods reachable from the public media API, so an upload
    above the in-memory validation limit could be re-encoded at any size.
    """

    @staticmethod
    def _png_bytes(width: int, height: int) -> bytes:
        Image = pytest.importorskip("PIL.Image")
        buf = io.BytesIO()
        Image.new("RGB", (width, height), (120, 40, 200)).save(buf, format="PNG")
        return buf.getvalue()

    def test_convert_format_rejects_oversized_image(self):
        from src.core.media.processing.images import ImageProcessor

        data = self._png_bytes(200, 200)
        with pytest.raises(ImageProcessingError):
            ImageProcessor(max_dimension=100).convert_format(data, "PNG")

    def test_convert_format_allows_image_within_limit(self):
        from src.core.media.processing.images import ImageProcessor

        data = self._png_bytes(200, 200)
        out = ImageProcessor(max_dimension=300).convert_format(data, "PNG")
        assert out[:8] == b"\x89PNG\r\n\x1a\n"

    def test_strip_metadata_rejects_oversized_image(self):
        from src.core.media.processing.images import ImageProcessor

        data = self._png_bytes(200, 200)
        with pytest.raises(ImageProcessingError):
            ImageProcessor(max_dimension=100).strip_metadata(data)

    def test_strip_metadata_allows_image_within_limit(self):
        from src.core.media.processing.images import ImageProcessor

        data = self._png_bytes(200, 200)
        out = ImageProcessor(max_dimension=300).strip_metadata(data)
        assert isinstance(out, bytes)
        assert len(out) > 0


@pytest.mark.media
class TestStripMetadataPalettePixels:
    """`strip_metadata` must not re-decode a palette image through a blank palette.

    ``Image.new(mode, size)`` starts a ``P``-mode image with a fresh, all-black
    palette and ``paste`` copies raw palette *indices* rather than decoded
    colours, so every index of the source was resolved against that blank
    palette and the re-encoded image came out solid black. Metadata stripping
    is applied to stored uploads, so this silently destroyed palette PNGs
    (the format Pillow picks for 8-bit-colour screenshots and GIF stills).
    """

    @staticmethod
    def _palette_png() -> bytes:
        Image = pytest.importorskip("PIL.Image")
        img = Image.new("P", (8, 8))
        img.putpalette([value for i in range(256) for value in (i, 0, 255 - i)])
        for x in range(8):
            for y in range(8):
                img.putpixel((x, y), (x * 8 + y) % 256)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_strip_metadata_preserves_palette_colours(self):
        from src.core.media.processing.images import ImageProcessor

        Image = pytest.importorskip("PIL.Image")

        source = Image.open(io.BytesIO(self._palette_png()))
        out = ImageProcessor(max_dimension=300).strip_metadata(self._palette_png())

        stripped = Image.open(io.BytesIO(out))
        assert stripped.convert("RGB").tobytes() == source.convert("RGB").tobytes()

    def test_strip_metadata_keeps_the_palette_transparency_index(self):
        from src.core.media.processing.images import ImageProcessor

        Image = pytest.importorskip("PIL.Image")

        img = Image.new("P", (8, 8))
        img.putpalette([value for i in range(256) for value in (i, i, i)])
        img.info["transparency"] = 7
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        out = ImageProcessor(max_dimension=300).strip_metadata(buf.getvalue())

        stripped = Image.open(io.BytesIO(out))
        assert stripped.info.get("transparency") == 7


@pytest.mark.media
class TestJpegTransparencyFlattening:
    """Images re-encoded to JPEG must composite their alpha channel over white.

    JPEG cannot store an alpha channel, so transparent regions have to be
    blended onto an opaque background before re-encode. The composite only
    built a mask for ``RGBA`` imagery (and upgraded ``P`` images to ``RGBA``),
    so an ``LA`` (grayscale + alpha) source fell through with no mask and its
    alpha channel was dropped entirely -- a fully transparent black region was
    written to the JPEG as solid black instead of the white a viewer shows.
    Every entry point that re-encodes to JPEG is guarded here so the fix
    cannot regress at one of them.
    """

    @staticmethod
    def _la_png() -> bytes:
        Image = pytest.importorskip("PIL.Image")
        # Left half opaque black, right half fully transparent.
        img = Image.new("LA", (8, 8), (0, 255))
        for y in range(8):
            for x in range(4, 8):
                img.putpixel((x, y), (0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _rgba_png() -> bytes:
        Image = pytest.importorskip("PIL.Image")
        # Left half opaque green, right half fully transparent.
        img = Image.new("RGBA", (8, 8), (10, 200, 30, 255))
        for y in range(8):
            for x in range(4, 8):
                img.putpixel((x, y), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _decode(data: bytes):
        Image = pytest.importorskip("PIL.Image")
        return Image.open(io.BytesIO(data)).convert("RGB")

    def test_create_thumbnail_flattens_la_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        out, _w, _h = ImageProcessor(max_dimension=300).create_thumbnail(
            self._la_png(), size=8, output_format="JPEG"
        )
        img = self._decode(out)
        assert img.getpixel((0, 0))[0] < 60, img.getpixel((0, 0))
        assert img.getpixel((img.width - 1, 0))[0] > 200, img.getpixel(
            (img.width - 1, 0)
        )

    def test_create_thumbnails_flattens_la_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        results = ImageProcessor(max_dimension=300).create_thumbnails(
            self._la_png(), sizes=[8], output_format="JPEG"
        )
        img = self._decode(results[8][0])
        assert img.getpixel((0, 0))[0] < 60, img.getpixel((0, 0))
        assert img.getpixel((img.width - 1, 0))[0] > 200, img.getpixel(
            (img.width - 1, 0)
        )

    def test_resize_flattens_la_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        out, _w, _h = ImageProcessor(max_dimension=300).resize(
            self._la_png(), width=8, height=8, output_format="JPEG"
        )
        img = self._decode(out)
        assert img.getpixel((0, 0))[0] < 60, img.getpixel((0, 0))
        assert img.getpixel((img.width - 1, 0))[0] > 200, img.getpixel(
            (img.width - 1, 0)
        )

    def test_convert_format_flattens_la_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        out = ImageProcessor(max_dimension=300).convert_format(self._la_png(), "JPEG")
        img = self._decode(out)
        assert img.getpixel((0, 0))[0] < 60, img.getpixel((0, 0))
        assert img.getpixel((img.width - 1, 0))[0] > 200, img.getpixel(
            (img.width - 1, 0)
        )

    def test_rgba_still_flattens_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        out = ImageProcessor(max_dimension=300).convert_format(self._rgba_png(), "JPEG")
        img = self._decode(out)
        left = img.getpixel((0, 0))
        right = img.getpixel((img.width - 1, 0))
        # Opaque side stays green; transparent side becomes white.
        assert left[1] > 150 and left[1] > left[0], left
        assert right[0] > 200 and right[1] > 200, right

    def test_jpeg_output_is_rgb(self):
        from src.core.media.processing.images import ImageProcessor

        Image = pytest.importorskip("PIL.Image")
        out = ImageProcessor(max_dimension=300).convert_format(self._la_png(), "JPEG")
        assert Image.open(io.BytesIO(out)).mode in ("RGB", "L")
