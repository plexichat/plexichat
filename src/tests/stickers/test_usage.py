"""
Tests for sticker usage tracking.
"""

import pytest
import uuid
from src.core.stickers import StickerFormat, StickerNotFoundError


class TestStickerUsage:
    """Tests for sticker usage tracking."""

    def test_send_sticker_tracks_usage(self, db_and_modules):
        """Test sending a sticker tracks usage."""
        db, auth, messaging, servers, stickers = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"usage_owner_{unique_id}",
            email=f"usage_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        other = auth.register(
            username=f"usage_other_{unique_id}",
            email=f"usage_other_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers.create_server(owner.id, f"Usage Server {unique_id}")
        servers.add_member(server.id, other.id)

        pack = stickers.create_pack(
            user_id=owner.id,
            name="Usage Pack",
            server_id=server.id
        )

        sticker = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="usage_test",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/usage.png",
            size=100000
        )

        dm = messaging.create_dm(owner.id, other.id)
        msg = messaging.send_message(owner.id, dm.id, "Check this sticker!")

        usage = stickers.send_sticker(owner.id, msg.id, sticker.id)

        assert usage is not None
        assert usage.sticker_id == sticker.id
        assert usage.user_id == owner.id
        assert usage.message_id == msg.id
        assert usage.used_at > 0

    def test_send_sticker_increments_count(self, db_and_modules):
        """Test sending sticker increments usage count."""
        db, auth, messaging, servers, stickers = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"count_owner_{unique_id}",
            email=f"count_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        other = auth.register(
            username=f"count_other_{unique_id}",
            email=f"count_other_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers.create_server(owner.id, f"Count Server {unique_id}")
        servers.add_member(server.id, other.id)

        pack = stickers.create_pack(
            user_id=owner.id,
            name="Count Pack",
            server_id=server.id
        )

        sticker = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="count_test",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/count.png",
            size=100000
        )

        initial = stickers.get_sticker(sticker.id)
        initial_count = initial.usage_count

        dm = messaging.create_dm(owner.id, other.id)
        msg1 = messaging.send_message(owner.id, dm.id, "First sticker")
        msg2 = messaging.send_message(owner.id, dm.id, "Second sticker")

        stickers.send_sticker(owner.id, msg1.id, sticker.id)
        stickers.send_sticker(owner.id, msg2.id, sticker.id)

        updated = stickers.get_sticker(sticker.id)
        assert updated.usage_count == initial_count + 2

    def test_send_sticker_nonexistent_fails(self, db_and_modules):
        """Test sending nonexistent sticker fails."""
        db, auth, messaging, servers, stickers = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"nonex_owner_{unique_id}",
            email=f"nonex_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        other = auth.register(
            username=f"nonex_other_{unique_id}",
            email=f"nonex_other_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(owner.id, other.id)
        msg = messaging.send_message(owner.id, dm.id, "Test message")

        with pytest.raises(StickerNotFoundError):
            stickers.send_sticker(owner.id, msg.id, 999999999)
