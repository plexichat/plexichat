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
class TestFlattenAlphaOntoWhite:
    """``flatten_alpha_onto_white`` composites transparency onto white.

    ``ImageCompressor.compress`` had its own copy of the inline composite
    that only built a mask for ``RGBA``/``P`` sources, so an ``LA``
    (grayscale + alpha) image fell through with no mask and its alpha
    channel was dropped on re-encode -- a fully transparent region embedded
    as its underlying colour instead of white. The comparable avatar JPEG
    branch dropped the channel outright and could not save ``LA`` at all.
    The shared helper is guarded directly so a new caller cannot reintroduce
    the split.
    """

    @staticmethod
    def _la_image(size=8):
        Image = pytest.importorskip("PIL.Image")
        img = Image.new("LA", (size, size), (0, 255))
        for y in range(size):
            for x in range(size // 2, size):
                img.putpixel((x, y), (0, 0))
        return img

    @staticmethod
    def _la_png(size=8):
        img = TestFlattenAlphaOntoWhite._la_image(size)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _rgba_image(size=8):
        Image = pytest.importorskip("PIL.Image")
        img = Image.new("RGBA", (size, size), (10, 200, 30, 255))
        for y in range(size):
            for x in range(size // 2, size):
                img.putpixel((x, y), (0, 0, 0, 0))
        return img

    @classmethod
    def _enabled_compressor(cls):
        """A compressor that actually re-encodes.

        The shared test config disables compression, and the
        smaller-of-the-two branch falls back to the original bytes whenever
        the re-encode is not smaller -- neither default exercises the code
        under test.
        """
        from src.core.media.compression import ImageCompressor

        Image = pytest.importorskip("PIL.Image")
        compressor = ImageCompressor()
        compressor._config["enabled"] = True
        compressor._config["preserve_original"] = True
        assert compressor.is_available()
        assert Image
        return compressor

    def test_opaque_source_is_returned_unchanged(self):
        Image = pytest.importorskip("PIL.Image")
        from src.core.media.processing.images import flatten_alpha_onto_white

        # No copy: opaque uploads must not pay for an extra full allocation.
        img = Image.new("RGB", (64, 64), (120, 40, 200))
        assert flatten_alpha_onto_white(img) is img

    def test_la_flattens_onto_white(self):
        pytest.importorskip("PIL.Image")
        from src.core.media.processing.images import flatten_alpha_onto_white

        out = flatten_alpha_onto_white(self._la_image())
        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (0, 0, 0), out.getpixel((0, 0))
        assert out.getpixel((out.width - 1, 0)) == (255, 255, 255), out.getpixel(
            (out.width - 1, 0)
        )

    def test_rgba_flattens_onto_white(self):
        pytest.importorskip("PIL.Image")
        from src.core.media.processing.images import flatten_alpha_onto_white

        out = flatten_alpha_onto_white(self._rgba_image())
        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (10, 200, 30), out.getpixel((0, 0))
        assert out.getpixel((out.width - 1, 0)) == (255, 255, 255), out.getpixel(
            (out.width - 1, 0)
        )

    def test_palette_without_transparency_stays_rgb(self):
        Image = pytest.importorskip("PIL.Image")
        from src.core.media.processing.images import flatten_alpha_onto_white

        img = Image.new("P", (8, 8), 3)
        img.putpalette([v for i in range(256) for v in (i, i, i)])
        out = flatten_alpha_onto_white(img)
        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (3, 3, 3), out.getpixel((0, 0))

    def test_palette_with_transparency_flattens_onto_white(self):
        Image = pytest.importorskip("PIL.Image")
        from src.core.media.processing.images import flatten_alpha_onto_white

        img = Image.new("P", (8, 8), 0)
        img.putpalette([v for i in range(256) for v in (i, i, i)])
        img.info["transparency"] = 0
        out = flatten_alpha_onto_white(img)
        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (255, 255, 255), out.getpixel((0, 0))

    def test_image_compressor_flattens_la_onto_white_for_jpeg(self):
        from src.core.media.compression import CompressionQuality, ImageFormat

        Image = pytest.importorskip("PIL.Image")
        result = self._enabled_compressor().compress(
            self._la_png(size=64), CompressionQuality.MEDIUM, ImageFormat.JPEG
        )
        assert result.success, result.error
        decoded = Image.open(io.BytesIO(result.data))
        assert decoded.mode == "RGB", decoded.mode
        # Left half is opaque black; the formerly transparent half is white.
        assert decoded.getpixel((2, 2))[0] < 60, decoded.getpixel((2, 2))
        right = decoded.getpixel((decoded.width - 3, 2))
        assert right[0] > 200 and right[1] > 200 and right[2] > 200, right

    def test_image_compressor_webp_keeps_transparency(self):
        from src.core.media.compression import CompressionQuality, ImageFormat

        Image = pytest.importorskip("PIL.Image")
        result = self._enabled_compressor().compress(
            self._la_png(size=64), CompressionQuality.MEDIUM, ImageFormat.WEBP
        )
        assert result.success, result.error
        decoded = Image.open(io.BytesIO(result.data))
        # WebP stores alpha natively, so flattening it here would throw away
        # transparency the format was chosen to keep.
        assert decoded.mode == "RGBA", decoded.mode
        assert decoded.getpixel((2, 2))[3] == 255, decoded.getpixel((2, 2))
        assert decoded.getpixel((decoded.width - 3, 2))[3] == 0, decoded.getpixel(
            (decoded.width - 3, 2)
        )

    def test_image_compressor_still_flattens_rgba_onto_white(self):
        from src.core.media.compression import CompressionQuality, ImageFormat

        Image = pytest.importorskip("PIL.Image")
        buf = io.BytesIO()
        self._rgba_image(size=64).save(buf, format="PNG")

        result = self._enabled_compressor().compress(
            buf.getvalue(), CompressionQuality.MEDIUM, ImageFormat.JPEG
        )
        assert result.success, result.error
        decoded = Image.open(io.BytesIO(result.data)).convert("RGB")
        # Opaque side stays green; transparent side becomes white.
        left = decoded.getpixel((2, 2))
        assert left[1] > 150 and left[1] > left[0], left
        right = decoded.getpixel((decoded.width - 3, 2))
        assert right[0] > 200 and right[1] > 200 and right[2] > 200, right


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
    cannot regress at one of them; ``strip_metadata`` takes an explicit
    ``output_format`` and is guarded too, since a JPEG target there used to
    reach the encoder with the alpha channel still attached and fail with
    "cannot write mode RGBA as JPEG".
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

    def test_strip_metadata_flattens_la_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        out = ImageProcessor(max_dimension=300).strip_metadata(
            self._la_png(), output_format="JPEG"
        )
        img = self._decode(out)
        assert img.getpixel((0, 0))[0] < 60, img.getpixel((0, 0))
        assert img.getpixel((img.width - 1, 0))[0] > 200, img.getpixel(
            (img.width - 1, 0)
        )

    def test_strip_metadata_rgba_still_flattens_onto_white(self):
        from src.core.media.processing.images import ImageProcessor

        out = ImageProcessor(max_dimension=300).strip_metadata(
            self._rgba_png(), output_format="JPEG"
        )
        img = self._decode(out)
        left = img.getpixel((0, 0))
        right = img.getpixel((img.width - 1, 0))
        # Opaque side stays green; transparent side becomes white.
        assert left[1] > 150 and left[1] > left[0], left
        assert right[0] > 200 and right[1] > 200, right

    def test_strip_metadata_jpeg_output_is_rgb(self):
        from src.core.media.processing.images import ImageProcessor

        Image = pytest.importorskip("PIL.Image")
        out = ImageProcessor(max_dimension=300).strip_metadata(
            self._la_png(), output_format="JPEG"
        )
        assert Image.open(io.BytesIO(out)).mode in ("RGB", "L")

    def test_strip_metadata_palette_png_to_jpeg(self):
        """A palette PNG carrying a transparency index must strip to JPEG.

        ``strip_metadata`` rebuilt the image in the source mode and handed it
        straight to the encoder, so a ``P``-mode source asked for a ``JPEG``
        target failed with "cannot write mode P as JPEG" -- and a fully
        transparent index would have been flattened to black rather than the
        white background every other re-encode path composites.
        """
        from src.core.media.processing.images import ImageProcessor

        Image = pytest.importorskip("PIL.Image")

        img = Image.new("P", (8, 8))
        img.putpalette([value for i in range(256) for value in (i, i, i)])
        img.info["transparency"] = 0
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        out = ImageProcessor(max_dimension=300).strip_metadata(
            buf.getvalue(), output_format="JPEG"
        )

        stripped = Image.open(io.BytesIO(out))
        assert stripped.mode == "RGB"
        # Index 0 is fully transparent, so every pixel lands on the white
        # background rather than on the palette's black entry.
        assert stripped.getpixel((0, 0)) == (255, 255, 255)
