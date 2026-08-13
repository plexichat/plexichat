"""
Regression tests for sticker audit fixes.

Covers:
- ST-6: the live-update dispatch must use a real server member id (the old
  hardcoded ``user_id=0`` always failed the membership check, silently
  suppressing ``GUILD_STICKERS_UPDATE`` broadcasts).
- ST-7: ``send_sticker`` always enforces that the referenced message belongs
  to the caller, even when the messaging module is unavailable.
"""

import pytest

from src.core.stickers.models import PackType, StickerFormat
from src.core.stickers.manager import StickerManager
from src.core.stickers.exceptions import (
    PermissionDeniedError,
    MessageNotFoundError,
    InvalidStickerFormatError,
    InvalidStickerNameError,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\x0d\x0a-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
SVG_BYTES = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"


class TestStickerDispatchMembership:
    def test_server_packs_require_real_membership(self, sticker_manager, test_db):
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
            "VALUES (1, 'S', 1, 1, 1)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
            "VALUES (1, 1, 1, 1, 1)"
        )
        sticker_manager.create_pack(1, "P", server_id=1, pack_type=PackType.SERVER)

        # A real member can list server packs.
        assert sticker_manager.get_server_packs(1, 1)

        # The old dispatch passed user_id=0, which is never a member -> this
        # must raise (otherwise the broadcast would be silently dropped).
        with pytest.raises(PermissionDeniedError):
            sticker_manager.get_server_packs(0, 1)


class TestStickerUsageOwnership:
    def _make_sticker(self, sticker_manager, user_id=1):
        pack = sticker_manager.create_pack(
            user_id, "Personal", pack_type=PackType.PERSONAL
        )
        return sticker_manager.add_sticker(
            user_id, pack.id, "hi", StickerFormat.PNG, "url", 10
        )

    def test_send_sticker_enforces_ownership_when_messaging_unavailable(
        self, sticker_manager, monkeypatch
    ):
        # Simulate the messaging module being unavailable.
        sticker_manager._messaging = None
        sticker = self._make_sticker(sticker_manager, user_id=1)

        real_fetch = sticker_manager._db.fetch_one

        def fake_fetch_one(query, params):
            if "msg_messages" in query:
                message_id, author_id = params
                if message_id == 99 and author_id == 1:
                    return {"1": 1}
                return None
            return real_fetch(query, params)

        monkeypatch.setattr(sticker_manager._db, "fetch_one", fake_fetch_one)

        # Authorized message -> ok.
        usage = sticker_manager.send_sticker(1, 99, sticker.id)
        assert usage is not None

        # Unknown / non-owned message -> rejected (no silent spoofing).
        with pytest.raises(MessageNotFoundError):
            sticker_manager.send_sticker(1, 999, sticker.id)

    def test_send_sticker_requires_existing_sticker(self, sticker_manager):
        sticker_manager._messaging = None
        with pytest.raises(Exception):
            sticker_manager.send_sticker(1, 99, 123456789)


class TestStickerUploadHardening:
    """ST-1: no orphaned media on failure.  ST-4: SVG rejected."""

    def _manager(self, test_db, messaging_manager, server_manager, media_manager):
        return StickerManager(
            db=test_db,
            messaging_module=messaging_manager,
            servers_module=server_manager,
            media_module=media_manager,
        )

    def test_svg_content_type_rejected(
        self, test_db, messaging_manager, server_manager, media_manager
    ):
        mgr = self._manager(test_db, messaging_manager, server_manager, media_manager)
        pack = mgr.create_pack(1, "P", pack_type=PackType.PERSONAL)
        with pytest.raises(InvalidStickerFormatError):
            mgr.create_sticker_from_file(
                1, pack.id, "ok", SVG_BYTES, "image/svg+xml", 10
            )

    def test_orphaned_media_cleaned_up_on_failure(
        self, test_db, messaging_manager, server_manager, media_manager
    ):
        mgr = self._manager(test_db, messaging_manager, server_manager, media_manager)
        pack = mgr.create_pack(1, "P", pack_type=PackType.PERSONAL)

        before = test_db.fetch_one("SELECT COUNT(*) AS c FROM media_files")["c"]
        with pytest.raises(InvalidStickerNameError):
            # A name containing spaces is invalid; the media uploaded for it
            # must be rolled back rather than left orphaned.
            mgr.create_sticker_from_file(
                1, pack.id, "bad name", PNG_BYTES, "image/png", 10
            )
        after = test_db.fetch_one("SELECT COUNT(*) AS c FROM media_files")["c"]
        assert after == before
