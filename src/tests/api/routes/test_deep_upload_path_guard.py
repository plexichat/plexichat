"""Regression tests: confined generated paths and header-safe filenames.

Covers the filename/path helpers used by avatars, soundboard, stickers and
attachments, including traversal attempts and header-injection payloads.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.avatars.manager.upload_guard import (
    PathGuardError,
    confined_path,
    content_disposition,
    header_safe_filename,
    sanitize_upload_filename,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_sanitize_upload_filename_strips_traversal_and_separators():
    assert sanitize_upload_filename("../../etc/passwd") == "passwd"
    assert sanitize_upload_filename("C:\\evil\\name.mp3") == "name.mp3"
    assert sanitize_upload_filename("..") == "file"
    assert sanitize_upload_filename(None, default="attachment") == "attachment"
    assert sanitize_upload_filename("evil\r\nX-Header: 1") == "evil_X-Header_1"


def test_confined_path_stays_inside_base(tmp_path):
    target = confined_path(tmp_path, "avatar.png")
    assert target.parent == tmp_path.resolve()
    assert target.name == "avatar.png"

    # Absolute and traversal-bearing components are rejected outright.
    with pytest.raises(PathGuardError):
        confined_path(tmp_path, "/etc/passwd")
    with pytest.raises(PathGuardError):
        confined_path(tmp_path, "..")


def test_confined_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-dir"
    try:
        outside.mkdir(exist_ok=True)
        link = tmp_path / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        with pytest.raises(PathGuardError):
            confined_path(tmp_path, "link", "payload.bin")
    finally:
        try:
            outside.rmdir()
        except OSError:
            pass


def test_content_disposition_is_header_safe():
    value = content_disposition('evil"\r\nX-Injected: 1.png')
    assert "\r" not in value and "\n" not in value
    assert '"' in value  # the quoted wrapper we control
    assert value == 'attachment; filename="evil_X-Injected_1.png"'
    assert content_disposition("avatar.png", disposition="bogus").startswith(
        "attachment;"
    )
    assert content_disposition("avatar.png", disposition="inline").startswith("inline;")
    assert header_safe_filename("a/b.png") == "b.png"


@pytest.mark.asyncio
async def test_avatar_serve_sets_safe_content_disposition():
    from src.api.routes.avatars import get_user_avatar

    fake_avatars = MagicMock()
    fake_avatars.get_user_avatar_checksum.return_value = "deadbeef"
    fake_avatars.get_user_avatar_data.return_value = (
        PNG_MAGIC + b"\x00" * 16,
        "image/png",
        "deadbeef",
    )
    request = MagicMock()
    request.headers = {}
    with patch("src.api.get_avatars", return_value=fake_avatars):
        response = await get_user_avatar(1, request)
    assert response.headers["content-disposition"] == 'inline; filename="avatar.png"'


def test_soundboard_generated_filename_is_confined(monkeypatch):
    from src.core.soundboard.manager import SoundboardManager

    media = MagicMock()
    media.upload_file.return_value = SimpleNamespace(url="/media/x.mp3", file_id=1)
    manager = SoundboardManager(db=MagicMock(), media_module=media)
    monkeypatch.setattr(manager, "upload_sound", lambda **kwargs: SimpleNamespace())

    manager.create_sound_from_upload(
        user_id=1,
        server_id=1,
        name="../../evil",
        file_data=b"ID3\x04\x00" + b"\x00" * 32,
        filename="C:\\evil\\name.mp3",
        content_type="audio/mpeg",
    )
    filename = media.upload_file.call_args.args[2]
    assert "/" not in filename and "\\" not in filename
    assert ".." not in filename
    assert filename == "evil.mp3"


def test_sticker_generated_filename_is_confined(monkeypatch):
    from src.core.stickers.manager import StickerManager
    from src.core.stickers.models import PackType

    media = MagicMock()
    media.upload_file.return_value = SimpleNamespace(
        url="/media/sticker.png", file_id=1, metadata=None
    )
    manager = StickerManager(db=MagicMock(), media_module=media)
    monkeypatch.setattr(
        manager,
        "get_pack",
        lambda *args, **kwargs: SimpleNamespace(
            pack_type=PackType.PERSONAL, server_id=None
        ),
    )
    monkeypatch.setattr(manager, "add_sticker", lambda **kwargs: SimpleNamespace())

    manager.create_sticker_from_file(
        1, 1, "../evil", PNG_MAGIC + b"\x00" * 16, "image/png"
    )
    filename = media.upload_file.call_args.args[2]
    assert "/" not in filename and "\\" not in filename
    assert ".." not in filename
