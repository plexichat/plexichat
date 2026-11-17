"""Consolidated regression tests driven by real malicious fixture files.

Fixtures live in ``src/tests/fixtures/malicious`` and cover the three classic
upload attacks: an SVG carrying a script, a binary/image polyglot with active
markup, and a zip-bomb header declaring a huge uncompressed size.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.avatars.errors import AvatarValidationError
from src.api.routes.avatars.validation import (
    is_serve_safe,
    validate_avatar_upload,
)
from src.core.avatars.manager.upload_guard import (
    DecompressionLimitError,
    UploadContentError,
    check_archive_entries,
    check_decompression_ratio,
    verify_upload_content,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "malicious"


@pytest.fixture
def svg_script() -> bytes:
    return (FIXTURES / "svg_script.svg").read_bytes()


@pytest.fixture
def png_polyglot() -> bytes:
    return (FIXTURES / "png_script_polyglot.png").read_bytes()


@pytest.fixture
def audio_polyglot() -> bytes:
    return (FIXTURES / "audio_script_polyglot.mp3").read_bytes()


@pytest.fixture
def zip_bomb() -> bytes:
    return (FIXTURES / "zip_bomb.zip").read_bytes()


def test_svg_script_fixture_rejected_by_avatar_validation(svg_script):
    assert b"<script" in svg_script
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/png", svg_script)
    with pytest.raises(UploadContentError):
        verify_upload_content("image/png", svg_script)
    assert is_serve_safe("image/png", svg_script) is False


@pytest.mark.asyncio
async def test_svg_script_fixture_rejected_by_emoji_route(svg_script):
    from src.api.routes.emojis import create_emoji

    reactions = MagicMock()
    file = SimpleNamespace(
        content_type="image/png",
        filename="wave.png",
        read=AsyncMock(return_value=svg_script),
    )
    with (
        patch("src.api.get_reactions", return_value=reactions),
        patch("src.api.routes.emojis._require_manage_emojis"),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_emoji("9", "wave", file, SimpleNamespace(user_id=1))
    assert exc.value.status_code == 400
    reactions.create_custom_emoji.assert_not_called()


def test_png_polyglot_fixture_rejected_across_surfaces(png_polyglot):
    assert png_polyglot.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"<script" in png_polyglot

    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/png", png_polyglot)
    assert is_serve_safe("image/png", png_polyglot) is False

    from src.core.stickers.exceptions import InvalidStickerFormatError
    from src.core.stickers.manager import StickerManager

    manager = StickerManager(db=MagicMock(), media_module=MagicMock())
    with pytest.raises(InvalidStickerFormatError):
        manager.create_sticker_from_file(1, 1, "bad", png_polyglot, "image/png")


def test_audio_polyglot_fixture_rejected_by_soundboard(audio_polyglot):
    from src.core.soundboard.exceptions import InvalidSoundFormatError
    from src.core.soundboard.manager import SoundboardManager

    manager = SoundboardManager(db=MagicMock(), media_module=MagicMock())
    with pytest.raises(InvalidSoundFormatError):
        manager.create_sound_from_upload(
            user_id=1,
            server_id=1,
            name="bad_sound",
            file_data=audio_polyglot,
            filename="bad.mp3",
            content_type="audio/mpeg",
        )


def test_zip_bomb_fixture_rejected(zip_bomb):
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zip_bomb)) as archive:
        entries = [(item.filename, item.file_size) for item in archive.infolist()]

    assert entries == [("payload.bin", 1 << 30)]
    with pytest.raises(DecompressionLimitError):
        check_archive_entries(entries)
    with pytest.raises(DecompressionLimitError):
        check_decompression_ratio(entries[0][1] // 100_000, entries[0][1])


def test_mime_mismatch_fixture_is_rejected():
    # GIF bytes declaring PNG must fail the magic-byte match.
    with pytest.raises(UploadContentError):
        verify_upload_content("image/png", b"GIF89a" + b"\x00" * 64)
