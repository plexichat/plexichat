"""Regression tests: decompression ratio caps and hard decoded-byte ceilings."""

from __future__ import annotations

import struct
import zlib
from unittest.mock import MagicMock

import pytest

from src.core.avatars.manager.upload_guard import (
    DecompressionLimitError,
    PathGuardError,
    check_archive_entries,
    check_decompression_ratio,
    check_image_decode_limits,
)


def _png_with_dimensions(width: int, height: int) -> bytes:
    """Craft a tiny PNG whose IHDR declares a very large canvas."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + chunk(b"IEND", b"")
    )


def test_ratio_cap_rejects_highly_compressed_payload():
    with pytest.raises(DecompressionLimitError):
        check_decompression_ratio(128, 128 * 100_000, label="image")


def test_hard_ceiling_applies_even_with_large_input():
    with pytest.raises(DecompressionLimitError):
        check_decompression_ratio(
            64 * 1024 * 1024, 512 * 1024 * 1024, hard_ceiling=256 * 1024 * 1024
        )


def test_small_compressible_image_is_allowed():
    assert check_decompression_ratio(150, 64 * 64 * 4) == 64 * 64 * 4


def test_frame_count_multiplies_decode_cost():
    # 1000x1000x4 bytes per frame x 100 frames is far above the ceiling.
    with pytest.raises(DecompressionLimitError):
        check_image_decode_limits(1024, 1000, 1000, frames=100)


def test_avatar_manager_rejects_dimension_bomb():
    from src.core.avatars.manager import AvatarManager

    manager = AvatarManager(db=MagicMock())
    bomb = _png_with_dimensions(4000, 4000)
    with pytest.raises(ValueError, match="decompression limit"):
        manager._process_image(bomb, "image/png")


def test_archive_entry_limits():
    with pytest.raises(DecompressionLimitError):
        check_archive_entries([("payload.bin", 10**12)])
    with pytest.raises(PathGuardError):
        check_archive_entries([("../../etc/passwd", 16)])
    assert check_archive_entries([("a.png", 128), ("b.png", 256)]) == 384
