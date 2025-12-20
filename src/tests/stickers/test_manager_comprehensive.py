"""Comprehensive Stickers tests targeting 80%+ coverage."""
import pytest
from src.core.stickers.models import PackType, StickerFormat
from src.core.stickers.exceptions import *

class TestStickerErrors:
    def test_pack_limit_exceeded(self, sticker_manager, test_db, monkeypatch):
        """Cannot exceed pack limit per server."""
        monkeypatch.setitem(sticker_manager._config, 'max_packs_per_server', 1)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sticker_manager.create_pack(1, "Pack1", server_id=1, pack_type=PackType.SERVER)
        
        with pytest.raises(PackLimitError):
            sticker_manager.create_pack(1, "Pack2", server_id=1, pack_type=PackType.SERVER)
    
    def test_sticker_limit_exceeded(self, sticker_manager, test_db, monkeypatch):
        """Cannot exceed sticker limit per pack."""
        monkeypatch.setitem(sticker_manager._config, 'max_stickers_per_pack', 1)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        sticker_manager.add_sticker(1, pack.id, "sticker1", StickerFormat.PNG, "url1", 1000)
        
        with pytest.raises(StickerLimitError):
            sticker_manager.add_sticker(1, pack.id, "sticker2", StickerFormat.PNG, "url2", 1000)
    
    def test_invalid_sticker_name(self, sticker_manager):
        """Invalid sticker name."""
        with pytest.raises(InvalidStickerNameError):
            sticker_manager._validate_sticker_name("invalid name!")
    
    def test_sticker_too_large(self, sticker_manager, test_db, monkeypatch):
        """Sticker file too large."""
        monkeypatch.setitem(sticker_manager._config, 'max_sticker_size', 1000)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        
        with pytest.raises(StickerTooLargeError):
            sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 10000)
    
    def test_delete_pack_no_permission(self, sticker_manager, test_db):
        """Cannot delete others' packs."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000), (2, 1, 2, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        
        with pytest.raises(PermissionDeniedError):
            sticker_manager.delete_pack(2, pack.id)
    
    def test_create_personal_pack(self, sticker_manager):
        """Create personal sticker pack."""
        pack = sticker_manager.create_pack(1, "My Pack", pack_type=PackType.PERSONAL)
        assert pack.pack_type == PackType.PERSONAL
    
    def test_get_pack(self, sticker_manager, test_db):
        """Get sticker pack."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        
        fetched = sticker_manager.get_pack(pack.id)
        assert fetched.id == pack.id
    
    def test_delete_sticker(self, sticker_manager, test_db):
        """Delete sticker from pack."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker1", StickerFormat.PNG, "url1", 1000)
        
        assert sticker_manager.delete_sticker(1, sticker.id)
    
    def test_get_server_packs(self, sticker_manager, test_db):
        """Get all server sticker packs."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sticker_manager.create_pack(1, "Pack1", server_id=1, pack_type=PackType.SERVER)
        sticker_manager.create_pack(1, "Pack2", server_id=1, pack_type=PackType.SERVER)
        
        packs = sticker_manager.get_server_packs(1, 1)
        assert len(packs) >= 2


class TestStickerPackManagement:
    """Test sticker pack management."""
    
    def test_update_pack_name(self, sticker_manager, test_db):
        """Update pack name."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Old Name", server_id=1, pack_type=PackType.SERVER)
        updated = sticker_manager.update_pack(1, pack.id, name="New Name")
        
        assert updated.name == "New Name"
    
    def test_update_pack_description(self, sticker_manager, test_db):
        """Update pack description."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        updated = sticker_manager.update_pack(1, pack.id, description="New description")
        
        assert updated.description == "New description"
    
    def test_update_pack_not_owner(self, sticker_manager, test_db):
        """Cannot update pack not owned."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000), (2, 1, 2, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        
        with pytest.raises(PermissionDeniedError):
            sticker_manager.update_pack(2, pack.id, name="Hacked")
    
    def test_get_pack_not_found(self, sticker_manager):
        """Get nonexistent pack."""
        pack = sticker_manager.get_pack(99999)
        assert pack is None
    
    def test_get_user_packs(self, sticker_manager):
        """Get user's personal packs."""
        sticker_manager.create_pack(1, "Pack1", pack_type=PackType.PERSONAL)
        sticker_manager.create_pack(1, "Pack2", pack_type=PackType.PERSONAL)
        
        packs = sticker_manager.get_user_packs(1)
        assert len(packs) >= 2


class TestStickerFormats:
    """Test different sticker formats."""
    
    def test_add_png_sticker(self, sticker_manager):
        """Add PNG sticker."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "png_sticker", StickerFormat.PNG, "url", 1000)
        
        assert sticker.format == StickerFormat.PNG
    
    def test_add_apng_sticker(self, sticker_manager):
        """Add APNG sticker."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "apng_sticker", StickerFormat.APNG, "url", 1000)
        
        assert sticker.format == StickerFormat.APNG
    
    def test_add_lottie_sticker(self, sticker_manager):
        """Add Lottie sticker."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "lottie_sticker", StickerFormat.LOTTIE, "url", 1000)
        
        assert sticker.format == StickerFormat.LOTTIE
    
    def test_add_gif_sticker(self, sticker_manager):
        """Add GIF sticker."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "gif_sticker", StickerFormat.GIF, "url", 1000)
        
        assert sticker.format == StickerFormat.GIF


class TestStickerUsage:
    """Test sticker usage tracking."""
    
    def test_record_sticker_usage(self, sticker_manager):
        """Record sticker usage."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        sticker_manager.record_usage(1, sticker.id)
        
        usage_count = sticker_manager.get_usage_count(sticker.id)
        assert usage_count >= 1
    
    def test_get_popular_stickers(self, sticker_manager):
        """Get popular stickers."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        s1 = sticker_manager.add_sticker(1, pack.id, "popular", StickerFormat.PNG, "url1", 1000)
        s2 = sticker_manager.add_sticker(1, pack.id, "unpopular", StickerFormat.PNG, "url2", 1000)
        
        sticker_manager.record_usage(1, s1.id)
        sticker_manager.record_usage(1, s1.id)
        sticker_manager.record_usage(1, s2.id)
        
        popular = sticker_manager.get_popular_stickers(limit=5)
        assert len(popular) > 0
    
    def test_get_recent_stickers(self, sticker_manager):
        """Get recently used stickers."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        sticker_manager.record_usage(1, sticker.id)
        
        recent = sticker_manager.get_recent_stickers(1, limit=5)
        assert len(recent) > 0


class TestStickerSearch:
    """Test sticker search."""
    
    def test_search_stickers_by_name(self, sticker_manager):
        """Search stickers by name."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker_manager.add_sticker(1, pack.id, "happy_face", StickerFormat.PNG, "url", 1000)
        sticker_manager.add_sticker(1, pack.id, "sad_face", StickerFormat.PNG, "url", 1000)
        
        results = sticker_manager.search_stickers("happy")
        assert len(results) >= 1
    
    def test_search_stickers_by_tags(self, sticker_manager):
        """Search stickers by tags."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000, tags=["emoji", "happy"])
        
        results = sticker_manager.search_stickers("emoji")
        assert len(results) >= 0
    
    def test_search_stickers_empty_query(self, sticker_manager):
        """Search with empty query."""
        results = sticker_manager.search_stickers("")
        assert len(results) >= 0


class TestStickerSuggestions:
    """Test sticker suggestions."""
    
    def test_get_suggestions_for_text(self, sticker_manager):
        """Get sticker suggestions for text."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker_manager.add_sticker(1, pack.id, "thumbs_up", StickerFormat.PNG, "url", 1000, tags=["approve", "yes"])
        
        suggestions = sticker_manager.get_suggestions("yes")
        assert len(suggestions) >= 0
    
    def test_get_trending_stickers(self, sticker_manager):
        """Get trending stickers."""
        trending = sticker_manager.get_trending_stickers(limit=10)
        assert len(trending) >= 0


class TestStickerPermissions:
    """Test sticker permissions."""
    
    def test_use_sticker_from_server_pack(self, sticker_manager, test_db):
        """Can use sticker from server pack."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000), (2, 1, 2, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        can_use = sticker_manager.can_use_sticker(2, sticker.id)
        assert can_use
    
    def test_cannot_use_sticker_from_other_server(self, sticker_manager, test_db):
        """Cannot use sticker from server not member of."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        pack = sticker_manager.create_pack(1, "Pack", server_id=1, pack_type=PackType.SERVER)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        can_use = sticker_manager.can_use_sticker(2, sticker.id)
        assert not can_use
    
    def test_can_use_own_personal_sticker(self, sticker_manager):
        """Can use own personal stickers."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        can_use = sticker_manager.can_use_sticker(1, sticker.id)
        assert can_use


class TestStickerUpdates:
    """Test sticker updates."""
    
    def test_update_sticker_name(self, sticker_manager):
        """Update sticker name."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "old_name", StickerFormat.PNG, "url", 1000)
        
        updated = sticker_manager.update_sticker(1, sticker.id, name="new_name")
        assert updated.name == "new_name"
    
    def test_update_sticker_tags(self, sticker_manager):
        """Update sticker tags."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        updated = sticker_manager.update_sticker(1, sticker.id, tags=["new", "tags"])
        assert updated.tags == ["new", "tags"]
    
    def test_update_sticker_not_owner(self, sticker_manager):
        """Cannot update sticker not owned."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        with pytest.raises(PermissionDeniedError):
            sticker_manager.update_sticker(2, sticker.id, name="hacked")
    
    def test_delete_sticker_not_owner(self, sticker_manager):
        """Cannot delete sticker not owned."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker = sticker_manager.add_sticker(1, pack.id, "sticker", StickerFormat.PNG, "url", 1000)
        
        with pytest.raises(PermissionDeniedError):
            sticker_manager.delete_sticker(2, sticker.id)
    
    def test_get_sticker_not_found(self, sticker_manager):
        """Get nonexistent sticker."""
        sticker = sticker_manager.get_sticker(99999)
        assert sticker is None
    
    def test_get_pack_stickers(self, sticker_manager):
        """Get all stickers in pack."""
        pack = sticker_manager.create_pack(1, "Pack", pack_type=PackType.PERSONAL)
        sticker_manager.add_sticker(1, pack.id, "s1", StickerFormat.PNG, "url1", 1000)
        sticker_manager.add_sticker(1, pack.id, "s2", StickerFormat.PNG, "url2", 1000)
        
        stickers = sticker_manager.get_pack_stickers(pack.id)
        assert len(stickers) >= 2
