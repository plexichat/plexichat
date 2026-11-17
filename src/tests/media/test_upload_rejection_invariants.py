"""Fail-closed upload invariants shared by every upload surface.

The application accepts client bytes plus a client-declared Content-Type on
avatars, server icons, custom emojis, soundboard sounds, stickers, plain media
attachments and artifact uploads. A caller may lie about either half. The
guards in :mod:`src.core.avatars.manager.upload_guard` are the single source of
truth that every surface must honour; this module pins the *invariants* they
must enforce so no surface can silently drift into accepting:

* a polyglot (a second container or active markup smuggled after valid magic),
* an oversized / decompression-bomb payload, or
* bytes whose magic signature disagrees with the declared MIME type.

Everything here is deterministic: fixed byte fixtures, pure guard calls and
manager/route calls against mocks. No wall-clock, network or shared state.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.core.avatars.manager.upload_guard import (
    DecompressionLimitError,
    PathGuardError,
    UploadContentError,
    assert_safe_media_payload,
    check_archive_entries,
    check_decompression_ratio,
    check_image_decode_limits,
    contains_active_content,
    detect_containers,
    is_polyglot,
    verify_upload_content,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGIC = b"GIF89a"
JPEG_MAGIC = b"\xff\xd8\xff"
ID3_MAGIC = b"ID3\x04\x00"


# ---------------------------------------------------------------------------
# Deterministic hostile payload fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svg_with_script() -> bytes:
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        b"<script>alert(document.cookie)</script>"
        b'<rect width="10" height="10"/></svg>'
    )


@pytest.fixture
def png_script_polyglot() -> bytes:
    """Valid PNG prefix with active markup appended in the tail."""
    return PNG_MAGIC + b"\x00" * 64 + b"<script>alert(1)</script>"


@pytest.fixture
def double_container_polyglot() -> bytes:
    """PNG magic with a GIF container smuggled later in the same payload."""
    return PNG_MAGIC + b"\x00" * 32 + GIF_MAGIC + b"\x00" * 16


@pytest.fixture
def audio_script_polyglot() -> bytes:
    return ID3_MAGIC + b"\x00" * 32 + b"<html><script>alert(1)</script></html>"


@pytest.fixture
def malicious_payloads(
    svg_with_script,
    png_script_polyglot,
    double_container_polyglot,
    audio_script_polyglot,
):
    return [
        svg_with_script,
        png_script_polyglot,
        double_container_polyglot,
        audio_script_polyglot,
    ]


# ---------------------------------------------------------------------------
# Guard primitive invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared, payload",
    [
        ("image/png", GIF_MAGIC + b"\x00" * 16),
        ("image/gif", PNG_MAGIC + b"\x00" * 16),
        ("audio/mpeg", PNG_MAGIC + b"\x00" * 16),
        ("image/jpeg", PNG_MAGIC + b"\x00" * 16),
    ],
)
def test_declared_mime_must_match_magic_bytes(declared, payload):
    with pytest.raises(UploadContentError):
        verify_upload_content(declared, payload)


@pytest.mark.parametrize(
    "payload_fixture",
    [
        "svg_with_script",
        "png_script_polyglot",
        "double_container_polyglot",
        "audio_script_polyglot",
    ],
)
def test_verify_rejects_every_hostile_payload(payload_fixture, request):
    with pytest.raises(UploadContentError):
        verify_upload_content("image/png", request.getfixturevalue(payload_fixture))


@pytest.mark.parametrize(
    "declared",
    ["image/svg+xml", "text/html", "application/xhtml+xml", "text/javascript"],
)
def test_verify_rejects_active_document_types(declared):
    with pytest.raises(UploadContentError):
        verify_upload_content(declared, b"<svg onload=alert(1)></svg>")


@pytest.mark.parametrize(
    "declared", ["", "application/octet-stream", "binary/octet-stream"]
)
def test_verify_rejects_unspecified_content_type(declared):
    with pytest.raises(UploadContentError):
        verify_upload_content(declared, PNG_MAGIC + b"\x00" * 8)


def test_assert_safe_media_payload_rejects_all_hostile_payloads(malicious_payloads):
    for payload in malicious_payloads:
        with pytest.raises(UploadContentError):
            assert_safe_media_payload(payload)


def test_hostile_payloads_are_detected_by_a_guard():
    png_script = PNG_MAGIC + b"\x00" * 64 + b"<script>alert(1)</script>"
    double_container = PNG_MAGIC + b"\x00" * 32 + GIF_MAGIC + b"\x00" * 16
    # Two binary containers -> polyglot; active markup -> active-content guard.
    assert is_polyglot(double_container) is True
    assert contains_active_content(png_script) is True
    assert "png" in detect_containers(PNG_MAGIC + b"\x00" * 8)
    assert "gif" in detect_containers(GIF_MAGIC + b"\x00" * 8)


@pytest.mark.parametrize(
    "declared, payload, expected",
    [
        ("image/png", PNG_MAGIC + b"\x00" * 32, "image/png"),
        ("image/gif", GIF_MAGIC + b"\x00" * 32, "image/gif"),
    ],
)
def test_verify_accepts_honest_payloads(declared, payload, expected):
    assert verify_upload_content(declared, payload) == expected


def test_decompression_ceiling_and_ratio_fail_closed():
    # A payload above the hard decoded ceiling is always refused.
    with pytest.raises(DecompressionLimitError):
        check_decompression_ratio(1_000, 300 * 1024 * 1024)
    # A compressed bomb with an extreme expansion ratio is refused.
    with pytest.raises(DecompressionLimitError):
        check_decompression_ratio(1_000, 64 * 1024 * 1024)
    # Honest small payloads pass through unchanged.
    assert check_decompression_ratio(512, 1024) == 1024
    with pytest.raises(DecompressionLimitError):
        check_decompression_ratio(-1, 10)


def test_image_decode_ceiling_is_enforced():
    with pytest.raises(DecompressionLimitError):
        check_image_decode_limits(100, 100_000, 100_000, frames=8)


def test_archive_cap_and_member_traversal_are_rejected():
    with pytest.raises(PathGuardError):
        check_archive_entries([("../../etc/passwd", 10)])
    with pytest.raises(PathGuardError):
        check_archive_entries([("C:\\evil\\name", 10)])
    with pytest.raises(DecompressionLimitError):
        check_archive_entries([("file.bin", 300 * 1024 * 1024)])


# ---------------------------------------------------------------------------
# Surface wiring: no surface may store a hostile payload
# ---------------------------------------------------------------------------


def test_media_upload_rejects_oversized_mismatched_and_executable(
    media_manager, test_user
):
    from src.core.media.exceptions import FileSizeError, FileTypeError

    uid = test_user.id
    # Oversized image -> FileSizeError (image cap is 10 MiB in test config).
    with pytest.raises(FileSizeError):
        media_manager.upload_file(
            user_id=uid,
            file_data=b"\x00" * (11 * 1024 * 1024),
            filename="big.png",
            content_type="image/png",
        )
    # GIF bytes declared as PNG -> signature mismatch -> FileTypeError.
    with pytest.raises(FileTypeError):
        media_manager.upload_file(
            user_id=uid,
            file_data=GIF_MAGIC + b"\x00" * 32,
            filename="spoof.png",
            content_type="image/png",
        )
    # Executable extension is hard-blocked.
    with pytest.raises(FileTypeError):
        media_manager.upload_file(
            user_id=uid,
            file_data=b"MZ\x90\x00" + b"\x00" * 64,
            filename="malware.exe",
            content_type="application/octet-stream",
        )


def test_avatar_validation_rejects_polyglot_and_oversized(
    monkeypatch, png_script_polyglot
):
    from src.api.routes.avatars import errors as avatar_errors
    from src.api.routes.avatars import validation as avatar_validation

    monkeypatch.setattr(avatar_validation, "max_file_size", lambda: 16)
    monkeypatch.setattr(
        avatar_validation, "allowed_types", lambda: ("image/png", "image/gif")
    )

    with pytest.raises(avatar_errors.AvatarValidationError):
        avatar_validation.validate_avatar_upload("image/png", png_script_polyglot)
    with pytest.raises(avatar_errors.AvatarValidationError):
        avatar_validation.validate_upload_size(PNG_MAGIC + b"\x00" * 32)
    with pytest.raises(avatar_errors.AvatarValidationError):
        avatar_validation.validate_avatar_upload("image/png", GIF_MAGIC + b"\x00" * 16)


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


def test_soundboard_rejects_polyglot_and_oversized(audio_script_polyglot):
    from src.core.soundboard.exceptions import (
        InvalidSoundFormatError,
        SoundboardError,
    )
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
    # Honest ID3 header padded past the 512 KiB cap -> oversized rejection.
    with pytest.raises(SoundboardError):
        manager.create_sound_from_upload(
            user_id=1,
            server_id=1,
            name="too_big",
            file_data=ID3_MAGIC + b"\x00" * (600 * 1024),
            filename="big.mp3",
            content_type="audio/mpeg",
        )


def test_sticker_rejects_polyglot_mismatch_and_oversized(png_script_polyglot):
    from src.core.stickers.exceptions import (
        InvalidStickerFormatError,
        StickerError,
    )
    from src.core.stickers.manager import StickerManager

    manager = StickerManager(db=MagicMock(), media_module=MagicMock())
    with pytest.raises(InvalidStickerFormatError):
        manager.create_sticker_from_file(1, 1, "bad", png_script_polyglot, "image/png")
    # GIF bytes declared as PNG are refused by magic-byte detection.
    with pytest.raises(InvalidStickerFormatError):
        manager.create_sticker_from_file(
            1, 1, "bad", GIF_MAGIC + b"\x00" * 32, "image/png"
        )
    # Honest PNG beyond the 512 KiB cap is refused before contacting storage.
    with pytest.raises(StickerError):
        manager.create_sticker_from_file(
            1, 1, "big", PNG_MAGIC + b"\x00" * (600 * 1024), "image/png"
        )
