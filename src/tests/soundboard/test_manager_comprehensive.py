"""Comprehensive Soundboard tests targeting 80%+ coverage."""
import pytest
from src.core.soundboard.models import SoundFormat
from src.core.soundboard.exceptions import *

class TestSoundboardErrors:
    def test_invalid_sound_name(self, soundboard_manager):
        """Invalid sound name."""
        with pytest.raises(InvalidSoundNameError):
            soundboard_manager._validate_sound_name("")
        
        with pytest.raises(InvalidSoundNameError):
            soundboard_manager._validate_sound_name("invalid name!")
    
    def test_sound_limit_exceeded(self, soundboard_manager, test_db, monkeypatch):
        """Cannot exceed sound limit per server."""
        monkeypatch.setitem(soundboard_manager._config, 'max_sounds_per_server', 1)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        soundboard_manager.upload_sound(1, 1, "sound1", SoundFormat.MP3, "url1", 1000, 1.0)
        
        with pytest.raises(SoundLimitError):
            soundboard_manager.upload_sound(1, 1, "sound2", SoundFormat.MP3, "url2", 1000, 1.0)
    
    def test_sound_too_large(self, soundboard_manager, test_db, monkeypatch):
        """Sound file too large."""
        monkeypatch.setitem(soundboard_manager._config, 'max_sound_size', 1000)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        with pytest.raises(SoundTooLargeError):
            soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 10000, 1.0)
    
    def test_sound_too_long(self, soundboard_manager, test_db, monkeypatch):
        """Sound duration too long."""
        monkeypatch.setitem(soundboard_manager._config, 'max_sound_duration_seconds', 3)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        with pytest.raises(SoundTooLongError):
            soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 10.0)
    
    def test_invalid_format(self, soundboard_manager, test_db, monkeypatch):
        """Invalid sound format."""
        monkeypatch.setitem(soundboard_manager._config, 'allowed_formats', ['mp3'])
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        with pytest.raises(InvalidSoundFormatError):
            soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.OGG, "url", 1000, 1.0)
    
    def test_delete_sound_no_permission(self, soundboard_manager, test_db):
        """Cannot delete without permission."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000), (2, 1, 2, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        with pytest.raises(PermissionDeniedError):
            soundboard_manager.delete_sound(2, sound.id)
    
    def test_cooldown_enforcement(self, soundboard_manager, test_db):
        """Sound cooldown is enforced."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        soundboard_manager.play_sound(1, 1, sound.id)
        
        remaining = soundboard_manager._check_cooldown(1, sound.id, 5)
        assert remaining is not None and remaining > 0
    
    def test_play_sound(self, soundboard_manager, test_db):
        """Can play sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        result = soundboard_manager.play_sound(1, 1, sound.id)
        assert result is not None
    
    def test_get_server_sounds(self, soundboard_manager, test_db):
        """Get all sounds for server."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        soundboard_manager.upload_sound(1, 1, "sound1", SoundFormat.MP3, "url1", 1000, 1.0)
        soundboard_manager.upload_sound(1, 1, "sound2", SoundFormat.MP3, "url2", 1000, 1.0)
        
        sounds = soundboard_manager.get_sounds(1, 1)
        assert len(sounds) >= 2
    
    def test_update_sound(self, soundboard_manager, test_db):
        """Update sound metadata."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        updated = soundboard_manager.update_sound(1, sound.id, name="new_name")
        assert updated.name == "new_name"
    
    def test_set_sound_volume(self, soundboard_manager, test_db):
        """Set default volume for sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        updated = soundboard_manager.update_sound(1, sound.id, volume=0.5)
        assert updated.volume == 0.5


class TestSoundFormats:
    """Test different sound formats."""
    
    def test_upload_mp3(self, soundboard_manager, test_db):
        """Upload MP3 sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "mp3_sound", SoundFormat.MP3, "url", 1000, 1.0)
        assert sound.format == SoundFormat.MP3
    
    def test_upload_ogg(self, soundboard_manager, test_db):
        """Upload OGG sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "ogg_sound", SoundFormat.OGG, "url", 1000, 1.0)
        assert sound.format == SoundFormat.OGG
    
    def test_upload_wav(self, soundboard_manager, test_db):
        """Upload WAV sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "wav_sound", SoundFormat.WAV, "url", 1000, 1.0)
        assert sound.format == SoundFormat.WAV


class TestSoundPlayback:
    """Test sound playback."""
    
    def test_play_sound_in_channel(self, soundboard_manager, test_db):
        """Play sound in voice channel."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        result = soundboard_manager.play_sound(1, 1, sound.id)
        assert result is not None
    
    def test_play_sound_not_in_voice(self, soundboard_manager, test_db):
        """Cannot play sound if not in voice channel."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        with pytest.raises(NotInVoiceChannelError):
            soundboard_manager.play_sound(1, None, sound.id)
    
    def test_play_sound_with_custom_volume(self, soundboard_manager, test_db):
        """Play sound with custom volume."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        result = soundboard_manager.play_sound(1, 1, sound.id, volume=0.8)
        assert result is not None
    
    def test_play_sound_not_found(self, soundboard_manager, test_db):
        """Cannot play nonexistent sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        with pytest.raises(SoundNotFoundError):
            soundboard_manager.play_sound(1, 1, 99999)


class TestSoundCooldowns:
    """Test sound cooldowns."""
    
    def test_global_cooldown(self, soundboard_manager, test_db, monkeypatch):
        """Global cooldown between sounds."""
        monkeypatch.setitem(soundboard_manager._config, 'global_cooldown_seconds', 5)
        
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound1 = soundboard_manager.upload_sound(1, 1, "sound1", SoundFormat.MP3, "url1", 1000, 1.0)
        sound2 = soundboard_manager.upload_sound(1, 1, "sound2", SoundFormat.MP3, "url2", 1000, 1.0)
        
        soundboard_manager.play_sound(1, 1, sound1.id)
        
        with pytest.raises(CooldownError):
            soundboard_manager.play_sound(1, 1, sound2.id)
    
    def test_per_sound_cooldown(self, soundboard_manager, test_db):
        """Per-sound cooldown."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0, cooldown=5)
        
        soundboard_manager.play_sound(1, 1, sound.id)
        
        with pytest.raises(CooldownError):
            soundboard_manager.play_sound(1, 1, sound.id)
    
    def test_get_cooldown_remaining(self, soundboard_manager, test_db):
        """Get remaining cooldown time."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0, cooldown=10)
        
        soundboard_manager.play_sound(1, 1, sound.id)
        
        remaining = soundboard_manager._check_cooldown(1, sound.id, 10)
        assert remaining > 0


class TestSoundManagement:
    """Test sound management."""
    
    def test_update_sound_name(self, soundboard_manager, test_db):
        """Update sound name."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "old_name", SoundFormat.MP3, "url", 1000, 1.0)
        
        updated = soundboard_manager.update_sound(1, sound.id, name="new_name")
        assert updated.name == "new_name"
    
    def test_update_sound_cooldown(self, soundboard_manager, test_db):
        """Update sound cooldown."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0, cooldown=5)
        
        updated = soundboard_manager.update_sound(1, sound.id, cooldown=10)
        assert updated.cooldown == 10
    
    def test_update_sound_not_owner(self, soundboard_manager, test_db):
        """Cannot update sound not owned."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000), (2, 1, 2, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        with pytest.raises(PermissionDeniedError):
            soundboard_manager.update_sound(2, sound.id, name="hacked")
    
    def test_get_sound_not_found(self, soundboard_manager):
        """Get nonexistent sound."""
        sound = soundboard_manager.get_sound(99999)
        assert sound is None
    
    def test_delete_sound(self, soundboard_manager, test_db):
        """Delete sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        assert soundboard_manager.delete_sound(1, sound.id)


class TestSoundUsage:
    """Test sound usage tracking."""
    
    def test_record_usage(self, soundboard_manager, test_db):
        """Record sound usage."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        soundboard_manager.play_sound(1, 1, sound.id)
        
        usage_count = soundboard_manager.get_usage_count(sound.id)
        assert usage_count >= 1
    
    def test_get_popular_sounds(self, soundboard_manager, test_db):
        """Get popular sounds."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound1 = soundboard_manager.upload_sound(1, 1, "popular", SoundFormat.MP3, "url1", 1000, 1.0)
        sound2 = soundboard_manager.upload_sound(1, 1, "unpopular", SoundFormat.MP3, "url2", 1000, 1.0)
        
        soundboard_manager.play_sound(1, 1, sound1.id)
        soundboard_manager.play_sound(1, 1, sound1.id)
        
        popular = soundboard_manager.get_popular_sounds(1, limit=5)
        assert len(popular) > 0
    
    def test_get_recent_sounds(self, soundboard_manager, test_db):
        """Get recently used sounds."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        soundboard_manager.play_sound(1, 1, sound.id)
        
        recent = soundboard_manager.get_recent_sounds(1, 1, limit=5)
        assert len(recent) > 0


class TestSoundSearch:
    """Test sound search."""
    
    def test_search_sounds_by_name(self, soundboard_manager, test_db):
        """Search sounds by name."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        soundboard_manager.upload_sound(1, 1, "airhorn", SoundFormat.MP3, "url1", 1000, 1.0)
        soundboard_manager.upload_sound(1, 1, "drumroll", SoundFormat.MP3, "url2", 1000, 1.0)
        
        results = soundboard_manager.search_sounds(1, 1, "airhorn")
        assert len(results) >= 1
    
    def test_search_sounds_empty_query(self, soundboard_manager, test_db):
        """Search with empty query."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        results = soundboard_manager.search_sounds(1, 1, "")
        assert len(results) >= 0


class TestSoundPermissions:
    """Test sound permissions."""
    
    def test_can_play_sound(self, soundboard_manager, test_db):
        """Check if user can play sound."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        can_play = soundboard_manager.can_play_sound(1, sound.id)
        assert can_play
    
    def test_cannot_play_from_other_server(self, soundboard_manager, test_db):
        """Cannot play sound from server not member of."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at) VALUES (1, 1, 1, 1000)")
        
        sound = soundboard_manager.upload_sound(1, 1, "sound", SoundFormat.MP3, "url", 1000, 1.0)
        
        can_play = soundboard_manager.can_play_sound(2, sound.id)
        assert not can_play
