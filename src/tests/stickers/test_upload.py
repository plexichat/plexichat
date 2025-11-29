"""
Tests for sticker upload and management.
"""

import pytest
import uuid
from src.core.stickers import (
    StickerNotFoundError,
    InvalidStickerNameError,
    InvalidStickerFormatError,
    StickerTooLargeError,
    StickerLimitError,
    StickerFormat,
)


class TestAddSticker:
    """Tests for adding stickers to packs."""

    def test_add_sticker_success(self, server_with_pack):
        """Test adding a sticker successfully."""
        owner, server, pack, stickers, servers = server_with_pack

        sticker = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="happy_cat",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/happy_cat.png",
            size=100000,
            tags=["happy", "cat"],
            related_emoji="smile"
        )

        assert sticker is not None
        assert sticker.name == "happy_cat"
        assert sticker.format == StickerFormat.PNG
        assert sticker.pack_id == pack.id
        assert "happy" in sticker.tags
        assert sticker.related_emoji == "smile"

    def test_add_sticker_apng_format(self, server_with_pack):
        """Test adding an animated PNG sticker."""
        owner, server, pack, stickers, servers = server_with_pack

        sticker = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="animated_cat",
            format=StickerFormat.APNG,
            url="https://cdn.example.com/stickers/animated.apng",
            size=200000
        )

        assert sticker.format == StickerFormat.APNG

    def test_add_sticker_lottie_format(self, server_with_pack):
        """Test adding a Lottie JSON sticker."""
        owner, server, pack, stickers, servers = server_with_pack

        sticker = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="lottie_anim",
            format=StickerFormat.LOTTIE,
            url="https://cdn.example.com/stickers/anim.json",
            size=50000
        )

        assert sticker.format == StickerFormat.LOTTIE

    def test_add_sticker_invalid_name_fails(self, server_with_pack):
        """Test adding sticker with invalid name fails."""
        owner, server, pack, stickers, servers = server_with_pack

        with pytest.raises(InvalidStickerNameError):
            stickers.add_sticker(
                user_id=owner.id,
                pack_id=pack.id,
                name="invalid name with spaces",
                format=StickerFormat.PNG,
                url="https://cdn.example.com/stickers/test.png",
                size=100000
            )

    def test_add_sticker_empty_name_fails(self, server_with_pack):
        """Test adding sticker with empty name fails."""
        owner, server, pack, stickers, servers = server_with_pack

        with pytest.raises(InvalidStickerNameError):
            stickers.add_sticker(
                user_id=owner.id,
                pack_id=pack.id,
                name="",
                format=StickerFormat.PNG,
                url="https://cdn.example.com/stickers/test.png",
                size=100000
            )

    def test_add_sticker_too_large_fails(self, server_with_pack):
        """Test adding sticker that exceeds size limit fails."""
        owner, server, pack, stickers, servers = server_with_pack

        with pytest.raises(StickerTooLargeError) as exc_info:
            stickers.add_sticker(
                user_id=owner.id,
                pack_id=pack.id,
                name="huge_sticker",
                format=StickerFormat.PNG,
                url="https://cdn.example.com/stickers/huge.png",
                size=10000000
            )

        assert exc_info.value.max_size == 524288


class TestGetSticker:
    """Tests for getting stickers."""

    def test_get_sticker_success(self, server_with_pack):
        """Test getting a sticker successfully."""
        owner, server, pack, stickers, servers = server_with_pack

        created = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="get_test",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/get_test.png",
            size=100000
        )

        retrieved = stickers.get_sticker(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == "get_test"

    def test_get_sticker_nonexistent(self, server_with_pack):
        """Test getting nonexistent sticker returns None."""
        owner, server, pack, stickers, servers = server_with_pack

        result = stickers.get_sticker(999999999)
        assert result is None

    def test_get_pack_stickers(self, server_with_pack):
        """Test getting all stickers in a pack."""
        owner, server, pack, stickers, servers = server_with_pack

        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="pack_sticker_1",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/1.png",
            size=100000
        )
        stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="pack_sticker_2",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/2.png",
            size=100000
        )

        pack_stickers = stickers.get_pack_stickers(owner.id, pack.id)

        assert len(pack_stickers) >= 2
        names = [s.name for s in pack_stickers]
        assert "pack_sticker_1" in names
        assert "pack_sticker_2" in names


class TestRemoveSticker:
    """Tests for removing stickers."""

    def test_remove_sticker_success(self, server_with_pack):
        """Test removing a sticker successfully."""
        owner, server, pack, stickers, servers = server_with_pack

        sticker = stickers.add_sticker(
            user_id=owner.id,
            pack_id=pack.id,
            name="to_remove",
            format=StickerFormat.PNG,
            url="https://cdn.example.com/stickers/remove.png",
            size=100000
        )

        result = stickers.remove_sticker(owner.id, sticker.id)
        assert result is True

        retrieved = stickers.get_sticker(sticker.id)
        assert retrieved is None

    def test_remove_sticker_nonexistent_fails(self, server_with_pack):
        """Test removing nonexistent sticker fails."""
        owner, server, pack, stickers, servers = server_with_pack

        with pytest.raises(StickerNotFoundError):
            stickers.remove_sticker(owner.id, 999999999)
