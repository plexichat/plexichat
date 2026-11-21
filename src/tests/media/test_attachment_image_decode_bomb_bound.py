"""Attachment image decodes must enforce the shared decoded-byte ceiling.

A few hundred bytes can declare a canvas that expands to hundreds of MB once
Pillow decodes it for metadata or thumbnails. Pillow's global pixel cap alone
still permits ~700 MB, so the main media pipeline must apply the same budget
the avatar/icon/emoji/sticker surfaces already use.
"""

import io
import struct
import zlib

import pytest
from PIL import Image

from src.core.media.exceptions import ImageProcessingError
from src.core.media.processing.images import ImageProcessor


def _png_with_declared_size(width: int, height: int) -> bytes:
    """Return a tiny PNG whose IHDR declares ``width`` x ``height`` pixels.

    No pixels are allocated: only the 13-byte IHDR header is forged and its
    CRC recomputed, so the payload stays under 100 bytes.
    """
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    data = bytearray(buf.getvalue())
    struct.pack_into(">II", data, 16, width, height)
    crc = zlib.crc32(b"IHDR" + bytes(data[16:29])) & 0xFFFFFFFF
    struct.pack_into(">I", data, 29, crc)
    return bytes(data)


def test_huge_canvas_payload_is_rejected_before_decode():
    payload = _png_with_declared_size(10000, 10000)  # 100M px -> 400 MB decoded
    assert len(payload) < 200
    with Image.open(io.BytesIO(payload)) as probe:
        assert (probe.width, probe.height) == (10000, 10000)

    with pytest.raises(ImageProcessingError, match="decoded-size budget"):
        ImageProcessor().get_metadata(payload)


def test_honest_small_image_still_accepted():
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="PNG")
    metadata = ImageProcessor().get_metadata(buf.getvalue())
    assert metadata.width == 32 and metadata.height == 32
