"""
GIF fidelity regression tests (findings M2/M4).

Covers:
- M2: an animated GIF avatar upload keeps more than one frame (frames are
  preserved with save_all + append_images, not collapsed to a static image).
- M4: a static transparent GIF keeps its GIF container and transparency, and
  the stored content_type stays image/gif (never silently image/jpeg).
"""

import io

import pytest

from src.core.avatars.manager import AvatarManager


def _make_animated_gif(frames=3, size=(32, 32)):
    """Create a real multi-frame animated GIF in memory."""
    from PIL import Image

    imgs = [Image.new("RGB", size, (i * 60 % 256, 0, 128)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=imgs[1:],
        duration=100,
        loop=0,
    )
    return buf.getvalue()


def _make_transparent_static_gif(size=(32, 32)):
    """Create a single-frame palette GIF with a transparent index."""
    from PIL import Image

    img = Image.new("P", size)
    # Index 0 = transparent marker colour, index 1 = visible white.
    img.putpalette([255, 0, 255, 255, 255, 255] + [0, 0, 0] * 254)
    pixels = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = 0 if (x + y) % 2 == 0 else 1
    img.info["transparency"] = 0
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


@pytest.fixture
def avatar_manager(db):
    """A configured AvatarManager bound to the test db."""
    manager = AvatarManager(db=db)
    manager.setup(db)
    return manager


class TestAnimatedGifUploadKeepsFrames:
    """M2: animated GIF uploads must keep more than one frame."""

    def test_process_image_keeps_all_frames(self, avatar_manager):
        from PIL import Image

        processed, _, _, animated = avatar_manager._process_image(
            _make_animated_gif(), "image/gif"
        )
        assert animated is True
        out = Image.open(io.BytesIO(processed))
        assert getattr(out, "n_frames", 1) > 1
        assert getattr(out, "n_frames", 1) == 3

    def test_upload_keeps_all_frames(self, avatar_manager, test_user):
        from PIL import Image

        result = avatar_manager.upload_user_avatar(
            test_user.id, _make_animated_gif(), "image/gif"
        )
        assert result["animated"] is True
        assert result["content_type"] == "image/gif"

        stored, content_type, _checksum = avatar_manager.get_user_avatar_data(
            test_user.id
        )
        assert content_type == "image/gif"
        assert stored[:6] in (b"GIF87a", b"GIF89a")
        out = Image.open(io.BytesIO(stored))
        assert getattr(out, "n_frames", 1) > 1


class TestTransparentStaticGif:
    """M4: static transparent GIFs must stay GIFs with transparency intact."""

    def test_process_image_stays_gif_with_transparency(self, avatar_manager):
        from PIL import Image

        processed, _, _, animated = avatar_manager._process_image(
            _make_transparent_static_gif(), "image/gif"
        )
        assert animated is False
        # Must NOT be silently re-encoded to lossy JPEG.
        assert processed[:6] in (b"GIF87a", b"GIF89a")
        assert not processed.startswith(b"\xff\xd8\xff")
        out = Image.open(io.BytesIO(processed))
        assert out.format == "GIF"
        assert "transparency" in out.info

    def test_upload_content_type_stays_gif(self, avatar_manager, test_user):
        from PIL import Image

        result = avatar_manager.upload_user_avatar(
            test_user.id, _make_transparent_static_gif(), "image/gif"
        )
        assert result["content_type"] == "image/gif"
        assert result["content_type"] != "image/jpeg"

        stored, content_type, _checksum = avatar_manager.get_user_avatar_data(
            test_user.id
        )
        assert content_type == "image/gif"
        assert stored[:6] in (b"GIF87a", b"GIF89a")
        assert not stored.startswith(b"\xff\xd8\xff")
        out = Image.open(io.BytesIO(stored))
        assert "transparency" in out.info
