"""
Tests for the avatars module.

Covers:
- Content-type validation and magic-byte detection (spoofing defense)
- Image processing (resize, format conversion, dimension limits)
- User avatar upload / retrieval / deletion lifecycle
- Server icon lifecycle
- Checksum computation
- Default SVG placeholder generation
- Config-driven limits (max size, max file size, allowed types)
"""

import hashlib
import io

import pytest

from src.core import avatars
from src.core.avatars.manager import AvatarManager
from src.utils.encryption import generate_snowflake_id


def _make_png(width=64, height=64, color=(255, 0, 0)):
    """Create a tiny real PNG in memory."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg(width=64, height=64):
    """Create a tiny real JPEG in memory."""
    from PIL import Image

    img = Image.new("RGB", (width, height), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def avatar_manager(db):
    """A configured AvatarManager bound to the test db."""
    manager = AvatarManager(db=db)
    manager.setup(db)
    return manager


class TestContentTypeValidation:
    """Test content-type allow/deny and magic-byte detection."""

    def test_allowed_types_accepted(self, avatar_manager):
        """JPEG/PNG/GIF/WebP must all validate."""
        for ct in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            assert avatar_manager._validate_content_type(ct) is True

    def test_unknown_type_rejected(self, avatar_manager):
        """Unexpected content types must be rejected."""
        assert avatar_manager._validate_content_type("text/html") is False
        assert (
            avatar_manager._validate_content_type("application/x-msdownload") is False
        )

    def test_case_insensitive(self, avatar_manager):
        """Content-type matching must be case-insensitive."""
        assert avatar_manager._validate_content_type("Image/PNG") is True

    def test_detect_png(self, avatar_manager):
        """PNG magic bytes must be detected."""
        data = _make_png()
        assert avatar_manager._detect_content_type(
            data, "application/octet-stream"
        ) == ("image/png")

    def test_detect_jpeg(self, avatar_manager):
        """JPEG magic bytes must be detected."""
        data = _make_jpeg()
        assert avatar_manager._detect_content_type(
            data, "application/octet-stream"
        ) == ("image/jpeg")

    def test_detect_gif(self, avatar_manager):
        """GIF magic bytes must be detected."""
        data = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
        assert avatar_manager._detect_content_type(data, "unknown") == "image/gif"

    def test_detect_fallback(self, avatar_manager):
        """Unrecognized bytes must fall back to the claimed type."""
        data = b"not an image at all"
        assert avatar_manager._detect_content_type(data, "image/png") == "image/png"

    def test_detect_webp_requires_webp_marker(self, avatar_manager):
        """RIFF without the WEBP marker must fall back."""
        data = b"RIFF\x10\x00\x00\x00XXXX"
        assert avatar_manager._detect_content_type(data, "image/jpeg") == "image/jpeg"


class TestImageProcessing:
    """Test image resize/conversion logic."""

    def test_oversized_image_resized(self, avatar_manager):
        """Images larger than max_size must be downscaled."""
        # Build a large PNG and shrink max_size via config
        from utils.config import _config_instance as loader

        loader.config["avatars"] = {"max_size": 32}
        loader._invalidate_cache()
        try:
            processed, width, height, animated = avatar_manager._process_image(
                _make_png(256, 128), "image/png"
            )
            assert width <= 32
            assert height <= 32
            assert animated is False
            assert processed  # non-empty
        finally:
            loader.config.pop("avatars", None)
            loader._invalidate_cache()

    def test_small_image_unchanged(self, avatar_manager):
        """Images under max_size must not grow."""
        processed, width, height, animated = avatar_manager._process_image(
            _make_png(16, 16), "image/png"
        )
        assert width == 16
        assert height == 16

    def test_jpeg_converted_to_jpeg(self, avatar_manager):
        """A small JPEG must stay JPEG (not force PNG)."""
        processed, width, height, animated = avatar_manager._process_image(
            _make_jpeg(24, 24), "image/jpeg"
        )
        assert width == 24
        assert height == 24

    def test_rgba_png_keeps_alpha(self, avatar_manager):
        """RGBA PNGs must not be converted to a lossy format."""
        from PIL import Image

        img = Image.new("RGBA", (32, 32), (255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

        processed, _, _, _ = avatar_manager._process_image(data, "image/png")
        # PNG output still starts with PNG magic
        assert processed[:8] == b"\x89PNG\r\n\x1a\n"

    def test_transparency_flattens_onto_white_for_jpeg(self, avatar_manager):
        """A JPEG target must composite alpha, never drop it.

        The JPEG branch re-encoded with ``convert("RGB")``, which discards
        the alpha channel: a fully transparent black region embedded as
        solid black instead of the white background a viewer shows, and an
        ``LA`` (grayscale + alpha) source could not be written as JPEG at
        all. Only an alpha-capable container that the magic-byte sniffers do
        not recognise (here a TIFF) reaches the JPEG branch with alpha still
        attached, which is exactly how an operator-allowed type slips past.
        """
        from PIL import Image

        img = Image.new("LA", (16, 16), (0, 255))
        for y in range(16):
            for x in range(8, 16):
                img.putpixel((x, y), (0, 0))
        buf = io.BytesIO()
        img.save(buf, format="TIFF")
        data = buf.getvalue()

        processed, width, height, animated = avatar_manager._process_image(
            data, "image/tiff"
        )
        assert width == 16 and height == 16
        assert animated is False

        decoded = Image.open(io.BytesIO(processed))
        assert decoded.format == "JPEG", decoded.format
        # Left half opaque black; formerly transparent half is white.
        assert decoded.getpixel((2, 2))[0] < 60, decoded.getpixel((2, 2))
        right = decoded.getpixel((decoded.width - 3, 2))
        assert right[0] > 200 and right[1] > 200 and right[2] > 200, right

    def test_invalid_image_rejected(self, avatar_manager):
        """Non-image bytes must raise ValueError."""
        with pytest.raises(ValueError):
            avatar_manager._process_image(b"garbage data here", "image/png")

    def test_oversized_pixel_count_rejected(self, avatar_manager):
        """Images exceeding max_pixels must be rejected."""
        from utils.config import _config_instance as loader

        loader.config["avatars"] = {"max_pixels": 100}
        loader._invalidate_cache()
        try:
            with pytest.raises(ValueError):
                avatar_manager._process_image(_make_png(64, 64), "image/png")
        finally:
            loader.config.pop("avatars", None)
            loader._invalidate_cache()


class TestChecksum:
    """Test checksum computation."""

    def test_checksum_stable(self, avatar_manager):
        """Same data must produce the same SHA-256 hex digest."""
        data = b"hello avatar"
        assert (
            avatar_manager._compute_checksum(data) == hashlib.sha256(data).hexdigest()
        )

    def test_checksum_differs(self, avatar_manager):
        """Different data must produce different digests."""
        assert avatar_manager._compute_checksum(
            b"a"
        ) != avatar_manager._compute_checksum(b"b")


class TestUserAvatarLifecycle:
    """Test the full user avatar CRUD cycle."""

    def test_upload_and_get(self, avatar_manager, test_user):
        """Upload must store the avatar and return metadata."""
        result = avatar_manager.upload_user_avatar(
            test_user.id, _make_png(), "image/png"
        )
        assert result["user_id"] == str(test_user.id)
        assert result["width"] > 0
        assert result["height"] > 0
        assert result["content_type"] == "image/png"

        meta = avatar_manager.get_user_avatar(test_user.id)
        assert meta is not None
        assert meta["user_id"] == str(test_user.id)

    def test_upload_then_update(self, avatar_manager, test_user):
        """Re-uploading must update, not duplicate."""
        avatar_manager.upload_user_avatar(test_user.id, _make_png(16, 16), "image/png")
        first = avatar_manager.get_user_avatar(test_user.id)

        avatar_manager.upload_user_avatar(test_user.id, _make_png(32, 32), "image/png")
        second = avatar_manager.get_user_avatar(test_user.id)

        assert first["id"] == second["id"]  # same row updated
        assert second["size"] != first["size"]  # content changed

    def test_get_data_roundtrip(self, avatar_manager, test_user):
        """get_user_avatar_data must return bytes + content type + checksum."""
        data = _make_png(24, 24)
        avatar_manager.upload_user_avatar(test_user.id, data, "image/png")
        avatar_bytes, content_type, checksum = avatar_manager.get_user_avatar_data(
            test_user.id
        )
        assert avatar_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        assert content_type == "image/png"
        assert len(checksum) == 64  # sha256 hex

    def test_url_present_after_upload(self, avatar_manager, test_user):
        """get_user_avatar_url must return the avatar URL after upload."""
        avatar_manager.upload_user_avatar(test_user.id, _make_png(), "image/png")
        url = avatar_manager.get_user_avatar_url(test_user.id)
        assert url == f"/api/v1/avatars/users/{test_user.id}"

    def test_checksum_lookup(self, avatar_manager, test_user):
        """get_user_avatar_checksum must return the stored checksum."""
        avatar_manager.upload_user_avatar(test_user.id, _make_png(), "image/png")
        checksum = avatar_manager.get_user_avatar_checksum(test_user.id)
        assert checksum is not None
        assert len(checksum) == 64

    def test_missing_avatar_none(self, avatar_manager, test_user):
        """Lookups for a user without an avatar must return None."""
        assert avatar_manager.get_user_avatar(test_user.id) is None
        assert avatar_manager.get_user_avatar_data(test_user.id) is None
        assert avatar_manager.get_user_avatar_url(test_user.id) is None
        assert avatar_manager.get_user_avatar_checksum(test_user.id) is None

    def test_delete_avatar(self, avatar_manager, test_user):
        """Delete must remove the row and return True."""
        avatar_manager.upload_user_avatar(test_user.id, _make_png(), "image/png")
        assert avatar_manager.delete_user_avatar(test_user.id) is True
        assert avatar_manager.get_user_avatar(test_user.id) is None

    def test_delete_missing_returns_false(self, avatar_manager, test_user):
        """Deleting a nonexistent avatar must return False."""
        assert avatar_manager.delete_user_avatar(test_user.id) is False

    def test_upload_rejects_bad_type(self, avatar_manager, test_user):
        """Uploading a disallowed content type must raise ValueError."""
        with pytest.raises(ValueError):
            avatar_manager.upload_user_avatar(test_user.id, _make_png(), "text/html")

    def test_upload_rejects_oversized_file(self, avatar_manager, test_user):
        """Uploading a file over max_file_size must raise ValueError."""
        from utils.config import _config_instance as loader

        loader.config["avatars"] = {"max_file_size": 100}
        loader._invalidate_cache()
        try:
            with pytest.raises(ValueError):
                avatar_manager.upload_user_avatar(
                    test_user.id, _make_png(), "image/png"
                )
        finally:
            loader.config.pop("avatars", None)
            loader._invalidate_cache()


class TestServerIconLifecycle:
    """Test server icon CRUD."""

    def test_upload_and_get_icon(self, avatar_manager, test_server):
        """Server icons must round-trip through the manager."""
        server, _owner = test_server
        result = avatar_manager.upload_server_icon(
            server.id, _make_jpeg(), "image/jpeg"
        )
        assert result["content_type"] == "image/jpeg"

        meta = avatar_manager.get_server_icon(server.id)
        assert meta is not None

        icon_bytes, content_type, checksum = avatar_manager.get_server_icon_data(
            server.id
        )
        assert icon_bytes[:2] == b"\xff\xd8"
        assert content_type == "image/jpeg"
        assert len(checksum) == 64

    def test_icon_url(self, avatar_manager, test_server):
        """Server icon URL must be exposed."""
        server, _owner = test_server
        avatar_manager.upload_server_icon(server.id, _make_png(), "image/png")
        url = avatar_manager.get_server_icon_url(server.id)
        assert url == f"/api/v1/avatars/servers/{server.id}"

    def test_delete_icon(self, avatar_manager, test_server):
        """Server icon delete must work."""
        server, _owner = test_server
        avatar_manager.upload_server_icon(server.id, _make_png(), "image/png")
        assert avatar_manager.delete_server_icon(server.id) is True
        assert avatar_manager.get_server_icon(server.id) is None


class TestDefaultSvg:
    """Test the default SVG placeholder generator."""

    def test_svg_structure(self, avatar_manager):
        """The SVG must contain the rect and initials text."""
        svg = avatar_manager.generate_default_svg("alice", "AL")
        assert svg.startswith("<svg")
        assert 'width="128"' in svg
        assert "AL" in svg

    def test_svg_deterministic_color(self, avatar_manager):
        """The same seed must produce the same SVG."""
        a = avatar_manager.generate_default_svg("bob", "BO")
        b = avatar_manager.generate_default_svg("bob", "BO")
        assert a == b

    def test_svg_seed_sensitive(self, avatar_manager):
        """Different seeds must produce different colors (SVGs differ)."""
        a = avatar_manager.generate_default_svg("carol", "CA")
        b = avatar_manager.generate_default_svg("dave", "DA")
        assert a != b


class TestModuleEntrypoints:
    """Test the module-level avatars API."""

    def test_not_setup_guard(self, monkeypatch):
        """Calls before setup must raise RuntimeError."""
        monkeypatch.setattr(avatars, "_manager", None)
        with pytest.raises(RuntimeError):
            avatars.get_user_avatar(1)

    def test_setup_and_upload_via_module(self, db, test_user):
        """The module API must route through to the manager."""
        avatars.setup(db)
        try:
            result = avatars.upload_user_avatar(test_user.id, _make_png(), "image/png")
            assert result["user_id"] == str(test_user.id)
            assert avatars.get_user_avatar_url(test_user.id) is not None
        finally:
            avatars._manager = None

    def test_snowflake_ids_are_unique(self):
        """generate_snowflake_id must produce unique values."""
        ids = {generate_snowflake_id() for _ in range(100)}
        assert len(ids) == 100
