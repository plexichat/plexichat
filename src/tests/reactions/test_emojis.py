"""
Tests for custom emoji functionality.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.core.reactions.models import CustomEmoji
from src.core.reactions.exceptions import (
    EmojiLimitError,
    EmojiNameExistsError,
    InvalidEmojiNameError,
    EmojiFileSizeError,
    InvalidEmojiFileError,
    PermissionDeniedError,
    CustomEmojiNotFoundError,
)


class TestEmojiNameValidation:
    """Tests for emoji name validation."""
    
    def test_valid_names(self, reactions_manager):
        """Test valid emoji names."""
        valid_names = ["pepe", "happy_cat", "test123", "a1", "emoji_name_here"]
        for name in valid_names:
            result = reactions_manager._validate_emoji_name(name)
            assert result == name.lower()
    
    def test_empty_name_rejected(self, reactions_manager):
        """Test empty name is rejected."""
        with pytest.raises(InvalidEmojiNameError):
            reactions_manager._validate_emoji_name("")
        with pytest.raises(InvalidEmojiNameError):
            reactions_manager._validate_emoji_name("   ")
    
    def test_too_short_name_rejected(self, reactions_manager):
        """Test name too short is rejected."""
        with pytest.raises(InvalidEmojiNameError):
            reactions_manager._validate_emoji_name("a")
    
    def test_too_long_name_rejected(self, reactions_manager):
        """Test name too long is rejected."""
        with pytest.raises(InvalidEmojiNameError):
            reactions_manager._validate_emoji_name("a" * 33)
    
    def test_invalid_characters_rejected(self, reactions_manager):
        """Test invalid characters are rejected."""
        invalid_names = ["emoji-name", "emoji.name", "emoji name", "EMOJI", "émoji"]
        for name in invalid_names:
            with pytest.raises(InvalidEmojiNameError):
                reactions_manager._validate_emoji_name(name)


class TestEmojiLimits:
    """Tests for emoji limit checking."""
    
    def test_static_limit_check(self, reactions_manager, mock_db):
        """Test static emoji limit is enforced."""
        mock_db.fetch_one.return_value = {"count": 50}
        
        with pytest.raises(EmojiLimitError) as exc_info:
            reactions_manager._check_emoji_limits(server_id=1, animated=False)
        
        assert exc_info.value.max_allowed == 50
        assert exc_info.value.current == 50
    
    def test_animated_limit_check(self, reactions_manager, mock_db):
        """Test animated emoji limit is enforced."""
        mock_db.fetch_one.return_value = {"count": 50}
        
        with pytest.raises(EmojiLimitError) as exc_info:
            reactions_manager._check_emoji_limits(server_id=1, animated=True)
        
        assert exc_info.value.max_allowed == 50
    
    def test_under_limit_passes(self, reactions_manager, mock_db):
        """Test under limit passes."""
        mock_db.fetch_one.return_value = {"count": 25}
        
        # Should not raise
        reactions_manager._check_emoji_limits(server_id=1, animated=False)


class TestCreateEmoji:
    """Tests for emoji creation."""
    
    def test_create_emoji_success(self, reactions_manager, mock_db, mock_servers, mock_media):
        """Test successful emoji creation."""
        mock_servers.has_permission.return_value = True
        mock_db.fetch_one.side_effect = [
            {"count": 10},  # limit check
            None,  # name uniqueness check
            {  # get_custom_emoji result
                "id": 123,
                "server_id": 1,
                "name": "test",
                "animated": 0,
                "url": "/media/test.png",
                "size": 1000,
                "width": 64,
                "height": 64,
                "created_by": 1,
                "available": 1,
                "created_at": 1704067200000,
            }
        ]
        mock_media.upload_file.return_value = MagicMock(
            url="/media/test.png",
            metadata={"width": 64, "height": 64}
        )
        
        emoji = reactions_manager.create_custom_emoji(
            user_id=1,
            server_id=1,
            name="test",
            image_data=b"PNG_DATA",
            content_type="image/png"
        )
        
        assert emoji.name == "test"
        assert emoji.url == "/media/test.png"
    
    def test_create_emoji_permission_denied(self, reactions_manager, mock_servers):
        """Test permission denied for emoji creation."""
        mock_servers.has_permission.return_value = False
        
        with pytest.raises(PermissionDeniedError):
            reactions_manager.create_custom_emoji(
                user_id=1,
                server_id=1,
                name="test",
                image_data=b"PNG_DATA",
                content_type="image/png"
            )
    
    def test_create_emoji_file_too_large(self, reactions_manager, mock_servers):
        """Test file size limit is enforced."""
        mock_servers.has_permission.return_value = True
        
        large_data = b"x" * (256 * 1024 + 1)  # Over 256KB
        
        with pytest.raises(EmojiFileSizeError):
            reactions_manager.create_custom_emoji(
                user_id=1,
                server_id=1,
                name="test",
                image_data=large_data,
                content_type="image/png"
            )
    
    def test_create_emoji_invalid_format(self, reactions_manager, mock_servers):
        """Test invalid file format is rejected."""
        mock_servers.has_permission.return_value = True
        
        with pytest.raises(InvalidEmojiFileError):
            reactions_manager.create_custom_emoji(
                user_id=1,
                server_id=1,
                name="test",
                image_data=b"DATA",
                content_type="image/bmp"
            )
    
    def test_create_emoji_name_exists(self, reactions_manager, mock_db, mock_servers):
        """Test duplicate name is rejected."""
        mock_servers.has_permission.return_value = True
        mock_db.fetch_one.side_effect = [
            {"count": 10},  # limit check
            {"id": 999},  # name exists
        ]
        
        with pytest.raises(EmojiNameExistsError):
            reactions_manager.create_custom_emoji(
                user_id=1,
                server_id=1,
                name="existing",
                image_data=b"PNG_DATA",
                content_type="image/png"
            )


class TestUpdateEmoji:
    """Tests for emoji updates."""
    
    def test_update_emoji_name(self, reactions_manager, mock_db, mock_servers):
        """Test updating emoji name."""
        mock_servers.has_permission.return_value = True
        mock_db.fetch_one.side_effect = [
            {  # get_custom_emoji (first call)
                "id": 123,
                "server_id": 1,
                "name": "old_name",
                "animated": 0,
                "url": "/media/test.png",
                "size": 1000,
                "width": 64,
                "height": 64,
                "created_by": 1,
                "available": 1,
                "created_at": 1704067200000,
            },
            None,  # name uniqueness check
            {  # get_custom_emoji (after update)
                "id": 123,
                "server_id": 1,
                "name": "new_name",
                "animated": 0,
                "url": "/media/test.png",
                "size": 1000,
                "width": 64,
                "height": 64,
                "created_by": 1,
                "available": 1,
                "created_at": 1704067200000,
            }
        ]
        
        emoji = reactions_manager.update_custom_emoji(
            user_id=1,
            emoji_id=123,
            name="new_name"
        )
        
        assert emoji.name == "new_name"
    
    def test_update_emoji_not_found(self, reactions_manager, mock_db):
        """Test updating non-existent emoji."""
        mock_db.fetch_one.return_value = None
        
        with pytest.raises(CustomEmojiNotFoundError):
            reactions_manager.update_custom_emoji(
                user_id=1,
                emoji_id=999,
                name="new_name"
            )


class TestDeleteEmoji:
    """Tests for emoji deletion."""
    
    def test_delete_emoji_success(self, reactions_manager, mock_db, mock_servers):
        """Test successful emoji deletion."""
        mock_servers.has_permission.return_value = True
        mock_db.fetch_one.return_value = {
            "id": 123,
            "server_id": 1,
            "name": "test",
            "animated": 0,
            "url": "/media/test.png",
            "size": 1000,
            "width": 64,
            "height": 64,
            "created_by": 1,
            "available": 1,
            "created_at": 1704067200000,
        }
        
        result = reactions_manager.delete_custom_emoji(user_id=1, emoji_id=123)
        
        assert result is True
        assert mock_db.execute.called
    
    def test_delete_emoji_not_found(self, reactions_manager, mock_db):
        """Test deleting non-existent emoji."""
        mock_db.fetch_one.return_value = None
        
        with pytest.raises(CustomEmojiNotFoundError):
            reactions_manager.delete_custom_emoji(user_id=1, emoji_id=999)


class TestGetEmojis:
    """Tests for emoji retrieval."""
    
    def test_get_server_emojis(self, reactions_manager, mock_db):
        """Test getting all server emojis."""
        mock_db.fetch_all.return_value = [
            {
                "id": 1,
                "server_id": 1,
                "name": "emoji1",
                "animated": 0,
                "url": "/media/1.png",
                "size": 1000,
                "width": 64,
                "height": 64,
                "created_by": 1,
                "available": 1,
                "created_at": 1704067200000,
            },
            {
                "id": 2,
                "server_id": 1,
                "name": "emoji2",
                "animated": 1,
                "url": "/media/2.gif",
                "size": 2000,
                "width": 64,
                "height": 64,
                "created_by": 1,
                "available": 1,
                "created_at": 1704067200000,
            },
        ]
        
        emojis = reactions_manager.get_server_custom_emojis(server_id=1)
        
        assert len(emojis) == 2
        assert emojis[0].name == "emoji1"
        assert emojis[1].animated is True
    
    def test_get_emoji_counts(self, reactions_manager, mock_db):
        """Test getting emoji counts."""
        mock_db.fetch_one.side_effect = [
            {"count": 25},  # static
            {"count": 10},  # animated
        ]
        
        counts = reactions_manager.get_emoji_counts(server_id=1)
        
        assert counts["static"] == 25
        assert counts["animated"] == 10
        assert counts["max_static"] == 50
        assert counts["max_animated"] == 50


# Fixtures
@pytest.fixture
def mock_db():
    """Create mock database."""
    db = MagicMock()
    db.fetch_one.return_value = None
    db.fetch_all.return_value = []
    return db


@pytest.fixture
def mock_servers():
    """Create mock servers module."""
    servers = MagicMock()
    servers.has_permission.return_value = True
    return servers


@pytest.fixture
def mock_media():
    """Create mock media module."""
    media = MagicMock()
    media.upload_file.return_value = MagicMock(
        url="/media/test.png",
        metadata={"width": 64, "height": 64}
    )
    return media


@pytest.fixture
def reactions_manager(mock_db, mock_servers, mock_media):
    """Create reactions manager with mocks."""
    from src.core.reactions.manager import ReactionManager
    
    with patch.object(ReactionManager, '_migrate_emoji_table'):
        manager = ReactionManager(
            db=mock_db,
            servers_module=mock_servers,
            media_module=mock_media
        )
    
    return manager
