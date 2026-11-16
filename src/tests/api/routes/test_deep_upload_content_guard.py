"""Regression tests: declared MIME vs magic bytes, polyglots, active content.

Malicious payload fixtures (SVG with a script, PNG/audio polyglots) are kept
in this module so every upload surface -- avatars, emojis, soundboard and
stickers -- can assert the same fail-closed behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.avatars.validation import (
    is_serve_safe,
    validate_avatar_upload,
)
from src.api.routes.avatars.errors import AvatarValidationError
from src.core.avatars.manager.upload_guard import (
    UploadContentError,
    contains_active_content,
    detect_containers,
    is_polyglot,
    verify_upload_content,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGIC = b"GIF89a"


# ---------------------------------------------------------------------------
# Malicious payload fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svg_with_script() -> bytes:
    """SVG document carrying an inline script and an event handler."""
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        b"<script>alert(document.cookie)</script>"
        b'<rect width="10" height="10"/></svg>'
    )


@pytest.fixture
def png_script_polyglot() -> bytes:
    """Valid PNG header with active markup appended in the tail."""
    return PNG_MAGIC + b"\x00" * 64 + b"<script>alert(1)</script>"


@pytest.fixture
def double_container_polyglot() -> bytes:
    """PNG magic with a GIF container smuggled later in the same payload."""
    return PNG_MAGIC + b"\x00" * 32 + GIF_MAGIC + b"\x00" * 16


@pytest.fixture
def audio_script_polyglot() -> bytes:
    """ID3 audio header followed by an injected HTML/script document."""
    return b"ID3\x04\x00" + b"\x00" * 32 + b"<html><script>alert(1)</script></html>"


# ---------------------------------------------------------------------------
# Guard primitives
# ---------------------------------------------------------------------------


def test_detect_containers_identifies_each_format():
    assert "png" in detect_containers(PNG_MAGIC + b"\x00" * 8)
    assert "gif" in detect_containers(GIF_MAGIC + b"\x00" * 8)
    assert "mp3" in detect_containers(b"ID3\x04\x00" + b"\x00" * 8)
    assert "ogg" in detect_containers(b"OggS" + b"\x00" * 8)


def test_polyglot_detection(double_container_polyglot, png_script_polyglot):
    assert is_polyglot(double_container_polyglot) is True
    # Active markup after a valid binary container is a polyglot too.
    assert contains_active_content(png_script_polyglot) is True


def test_verify_rejects_mime_magic_mismatch():
    # GIF bytes declared as PNG must fail.
    with pytest.raises(UploadContentError):
        verify_upload_content("image/png", GIF_MAGIC + b"\x00" * 16)


def test_verify_rejects_svg_with_script(svg_with_script):
    with pytest.raises(UploadContentError):
        verify_upload_content("image/png", svg_with_script)
    with pytest.raises(UploadContentError):
        verify_upload_content("image/svg+xml", svg_with_script)


def test_verify_rejects_polyglots(png_script_polyglot, double_container_polyglot):
    for payload in (png_script_polyglot, double_container_polyglot):
        with pytest.raises(UploadContentError):
            verify_upload_content("image/png", payload)


def test_verify_accepts_honest_payloads():
    assert verify_upload_content("image/png", PNG_MAGIC + b"\x00" * 32) == "image/png"
    assert verify_upload_content("image/gif", GIF_MAGIC + b"\x00" * 32) == "image/gif"


# ---------------------------------------------------------------------------
# Avatar surface
# ---------------------------------------------------------------------------


def test_avatar_validation_rejects_polyglot(png_script_polyglot):
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/png", png_script_polyglot)


def test_is_serve_safe_blocks_embedded_script(png_script_polyglot):
    assert is_serve_safe("image/png", png_script_polyglot) is False


@pytest.mark.asyncio
async def test_avatar_route_does_not_store_polyglot(png_script_polyglot):
    from src.api.routes.avatars import upload_my_avatar

    fake_avatars = MagicMock()
    file = SimpleNamespace(
        content_type="image/png", read=AsyncMock(return_value=png_script_polyglot)
    )
    with patch("src.api.get_avatars", return_value=fake_avatars):
        with pytest.raises(HTTPException) as exc:
            await upload_my_avatar(file, SimpleNamespace(user_id=1))
    assert exc.value.status_code == 400
    fake_avatars.upload_user_avatar.assert_not_called()


# ---------------------------------------------------------------------------
# Emoji surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_route_rejects_polyglot(png_script_polyglot):
    from src.api.routes.emojis import create_emoji

    fake_reactions = MagicMock()
    file = SimpleNamespace(
        content_type="image/png",
        filename="wave.png",
        read=AsyncMock(return_value=png_script_polyglot),
    )
    with (
        patch("src.api.get_reactions", return_value=fake_reactions),
        patch("src.api.routes.emojis._require_manage_emojis"),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_emoji("9", "wave", file, SimpleNamespace(user_id=1))
    assert exc.value.status_code == 400
    fake_reactions.create_custom_emoji.assert_not_called()


# ---------------------------------------------------------------------------
# Soundboard + sticker surfaces (manager-level)
# ---------------------------------------------------------------------------


def test_soundboard_rejects_audio_polyglot(audio_script_polyglot):
    from src.core.soundboard.exceptions import InvalidSoundFormatError
    from src.core.soundboard.manager import SoundboardManager

    manager = SoundboardManager(db=MagicMock(), media_module=MagicMock())
    with pytest.raises(InvalidSoundFormatError):
        manager.create_sound_from_upload(
            user_id=1,
            server_id=1,
            name="bad_sound",
            file_data=audio_script_polyglot,
            filename="bad.mp3",
            content_type="audio/mpeg",
        )


def test_sticker_rejects_polyglot(png_script_polyglot):
    from src.core.stickers.exceptions import InvalidStickerFormatError
    from src.core.stickers.manager import StickerManager

    manager = StickerManager(db=MagicMock(), media_module=MagicMock())
    with pytest.raises(InvalidStickerFormatError):
        manager.create_sticker_from_file(1, 1, "bad", png_script_polyglot, "image/png")
