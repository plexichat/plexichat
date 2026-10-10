"""
Regression tests for sticker bug fixes.

- S1: deleting a sticker or an entire pack must also remove the underlying
  media file. Previously the DB rows were deleted but the referenced media
  object was left orphaned in storage.
"""

from __future__ import annotations

import pytest

from src.core.stickers.manager import StickerManager
from src.core.stickers.models import PackType

pytestmark = pytest.mark.integration

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\x0d\x0a-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _manager(test_db, messaging_manager, server_manager, media_manager):
    return StickerManager(
        db=test_db,
        messaging_module=messaging_manager,
        servers_module=server_manager,
        media_module=media_manager,
    )


def test_delete_pack_purges_media(
    test_db, messaging_manager, server_manager, media_manager
):
    mgr = _manager(test_db, messaging_manager, server_manager, media_manager)
    pack = mgr.create_pack(1, "P", pack_type=PackType.PERSONAL)
    sticker = mgr.create_sticker_from_file(
        user_id=1,
        pack_id=pack.id,
        name="s1",
        image_data=PNG_BYTES,
        content_type="image/png",
    )

    filename = sticker.url.rsplit("/", 1)[-1]
    media_file = media_manager.get_file_by_filename(filename)
    assert media_file is not None
    fid = int(media_file.id)
    assert media_manager.get_file(fid) is not None

    mgr.delete_pack(1, pack.id)

    # The underlying media object must be gone (soft- or hard-deleted).
    assert media_manager.get_file(fid) is None


def test_delete_sticker_purges_media(
    test_db, messaging_manager, server_manager, media_manager
):
    mgr = _manager(test_db, messaging_manager, server_manager, media_manager)
    pack = mgr.create_pack(1, "P", pack_type=PackType.PERSONAL)
    sticker = mgr.create_sticker_from_file(
        user_id=1,
        pack_id=pack.id,
        name="s2",
        image_data=PNG_BYTES,
        content_type="image/png",
    )

    filename = sticker.url.rsplit("/", 1)[-1]
    media_file = media_manager.get_file_by_filename(filename)
    assert media_file is not None
    fid = int(media_file.id)
    assert media_manager.get_file(fid) is not None

    mgr.delete_sticker(1, sticker.id)

    assert media_manager.get_file(fid) is None
