"""Regression tests for the pHash image-decode resource guards.

The hashing path used to call ``Image.open`` and hand the result straight to
``imagehash``, which forces a full pixel decode. A tiny crafted file that only
declares huge dimensions in its header could therefore make the decoder
allocate gigabytes. The compressor and image processor already had
decompression-bomb checks; these tests keep the hashing path at parity.
"""

import io
import struct
import zlib

import pytest

Image = pytest.importorskip("PIL.Image")
imagehash = pytest.importorskip("imagehash")

from src.core.media import phash as phash_module  # noqa: E402
from src.core.security.hardening import HardeningViolation  # noqa: E402


def _png_bytes(size=(8, 8)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def _forged_header_png(width, height):
    """A tiny PNG whose IHDR declares ``width`` x ``height``.

    ``Image.open`` is lazy, so this is enough to exercise header-based guards
    without ever allocating the declared pixels.
    """
    data = bytearray(_png_bytes((8, 8)))
    struct.pack_into(">II", data, 16, width, height)
    struct.pack_into(">I", data, 29, zlib.crc32(bytes(data[12:29])) & 0xFFFFFFFF)
    return bytes(data)


def _patch_phash_config(monkeypatch, phash_config):
    def fake_get(key, default=None):
        if key == "media":
            return {"phash": phash_config}
        return default

    monkeypatch.setattr(phash_module.config, "get", fake_get)


def test_pixel_bomb_is_rejected_before_any_decode(monkeypatch):
    _patch_phash_config(monkeypatch, {"max_pixels": 10_000})
    data = _png_bytes((200, 200))  # 40,000 declared pixels > 10,000
    assert Image.open(io.BytesIO(data)).size == (200, 200)

    with pytest.raises(ValueError, match="decompression-bomb limit"):
        phash_module._open_validated_image(data)

    # No hash function may touch the pixels of a rejected image.
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)

    monkeypatch.setattr(imagehash, "phash", forbidden)
    monkeypatch.setattr(imagehash, "dhash", forbidden)
    monkeypatch.setattr(imagehash, "average_hash", forbidden)
    monkeypatch.setattr(imagehash, "whash", forbidden)

    for name in ("compute_phash", "compute_dhash", "compute_ahash", "compute_whash"):
        assert getattr(phash_module, name)(data) is None
    assert calls == []


def test_guard_does_not_depend_on_pillow_global(monkeypatch):
    """Disabling Pillow's mutable global must not disable our guard."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)
    data = _forged_header_png(30_000, 30_000)  # 900M declared pixels

    with pytest.raises(ValueError, match="decompression-bomb limit"):
        phash_module._open_validated_image(data)
    assert phash_module.compute_phash(data) is None


def test_normal_image_is_still_hashed():
    result = phash_module.compute_phash(_png_bytes((32, 32)))
    assert result is not None
    assert len(result) == 16  # default hash_size=8 -> 64 bits -> 16 hex chars


def test_hash_parameters_are_bounded(monkeypatch):
    _patch_phash_config(
        monkeypatch,
        {"hash_size": 10**9, "highfreq_factor": -5, "max_pixels": 10**12},
    )

    # Invalid config fails closed rather than driving a huge allocation.
    with pytest.raises(HardeningViolation):
        phash_module.get_config()

    _patch_phash_config(monkeypatch, {"hash_size": 16, "highfreq_factor": 8})
    cfg = phash_module.get_config()
    assert cfg["hash_size"] == 16
    assert cfg["highfreq_factor"] == 8
    assert cfg["max_pixels"] == phash_module.DEFAULT_MAX_IMAGE_PIXELS

    _patch_phash_config(monkeypatch, {})
    with pytest.raises(HardeningViolation):
        phash_module.compute_phash(_png_bytes((8, 8)), hash_size=10**9)
