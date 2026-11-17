"""Regression tests: EXIF orientation is applied and tracking metadata removed."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

from src.core.avatars.manager.processing import strip_image_metadata
from src.core.avatars.manager.upload_guard import contains_tracking_metadata


def test_strip_image_metadata_drops_dangerous_info_keys():
    from PIL import Image

    img = Image.new("P", (8, 8))
    img.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    img.info.update(
        {
            "transparency": 0,
            "duration": 100,
            "exif": b"Exif\x00\x00tracking",
            "xmp": b"<x:xmpmeta>device-id</x:xmpmeta>",
            "icc_profile": b"profile",
            "comment": b"secret",
        }
    )
    cleaned = strip_image_metadata(img)
    assert "transparency" in cleaned.info
    assert cleaned.info["duration"] == 100
    for key in ("exif", "xmp", "icc_profile", "comment"):
        assert key not in cleaned.info


def test_jpeg_exif_orientation_applied_and_metadata_stripped():
    from PIL import Image

    from src.core.avatars.manager import AvatarManager

    exif = Image.Exif()
    exif[274] = 6  # rotate 270 degrees -> swap width/height
    exif[270] = "device-serial-1234"
    source = Image.new("RGB", (8, 4), (255, 0, 0))
    buf = io.BytesIO()
    source.save(buf, format="JPEG", exif=exif.tobytes())
    payload = buf.getvalue()
    assert b"Exif\x00\x00" in payload

    manager = AvatarManager(db=MagicMock())
    processed, width, height, animated = manager._process_image(payload, "image/jpeg")

    assert b"Exif\x00\x00" not in processed
    assert (width, height) == (4, 8)
    assert animated is False
    decoded = Image.open(io.BytesIO(processed))
    assert decoded.size == (4, 8)


def test_animated_gif_keeps_frames_while_metadata_stripped():
    from PIL import Image

    from src.core.avatars.manager import AvatarManager

    frames = [Image.new("RGB", (16, 16), (i * 60, 0, 0)) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    manager = AvatarManager(db=MagicMock())
    processed, _, _, animated = manager._process_image(buf.getvalue(), "image/gif")
    assert animated is True
    assert Image.open(io.BytesIO(processed)).n_frames == 3


def test_tracking_metadata_detector():
    assert contains_tracking_metadata(b"junk Exif\x00\x00data") is True
    assert contains_tracking_metadata(b"junk <x:xmpmeta>data") is True
    assert contains_tracking_metadata(b"\x89PNG\r\n\x1a\n\x00\x00") is False
