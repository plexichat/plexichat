"""
Tests for sound upload.
"""

import pytest
import uuid
from src.core.soundboard import (
    SoundNotFoundError,
    InvalidSoundNameError,
    SoundTooLargeError,
    SoundTooLongError,
    SoundFormat,
)


class TestUploadSound:
    """Tests for uploading sounds."""

    def test_upload_sound_success(self, server_with_owner):
        """Test uploading a sound successfully."""
        owner, server, soundboard, servers = server_with_owner

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="airhorn",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/airhorn.mp3",
            size=100000,
            duration_seconds=2.0,
            emoji="loudspeaker",
            volume=0.8,
        )

        assert sound is not None
        assert sound.name == "airhorn"
        assert sound.format == SoundFormat.MP3
        assert sound.server_id == server.id
        assert sound.emoji == "loudspeaker"
        assert sound.volume == 0.8

    def test_upload_sound_ogg_format(self, server_with_owner):
        """Test uploading an OGG sound."""
        owner, server, soundboard, servers = server_with_owner

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="ogg_sound",
            format=SoundFormat.OGG,
            url="https://cdn.example.com/sounds/test.ogg",
            size=50000,
            duration_seconds=1.5,
        )

        assert sound.format == SoundFormat.OGG

    def test_upload_sound_invalid_name_fails(self, server_with_owner):
        """Test uploading sound with invalid name fails."""
        owner, server, soundboard, servers = server_with_owner

        with pytest.raises(InvalidSoundNameError):
            soundboard.upload_sound(
                user_id=owner.id,
                server_id=server.id,
                name="invalid name with spaces",
                format=SoundFormat.MP3,
                url="https://cdn.example.com/sounds/test.mp3",
                size=100000,
                duration_seconds=2.0,
            )

    def test_upload_sound_empty_name_fails(self, server_with_owner):
        """Test uploading sound with empty name fails."""
        owner, server, soundboard, servers = server_with_owner

        with pytest.raises(InvalidSoundNameError):
            soundboard.upload_sound(
                user_id=owner.id,
                server_id=server.id,
                name="",
                format=SoundFormat.MP3,
                url="https://cdn.example.com/sounds/test.mp3",
                size=100000,
                duration_seconds=2.0,
            )

    def test_upload_sound_too_large_fails(self, server_with_owner):
        """Test uploading sound that exceeds size limit fails."""
        owner, server, soundboard, servers = server_with_owner

        with pytest.raises(SoundTooLargeError) as exc_info:
            soundboard.upload_sound(
                user_id=owner.id,
                server_id=server.id,
                name="huge_sound",
                format=SoundFormat.MP3,
                url="https://cdn.example.com/sounds/huge.mp3",
                size=10000000,
                duration_seconds=2.0,
            )

        assert exc_info.value.max_size == 524288

    def test_upload_sound_too_long_fails(self, server_with_owner):
        """Test uploading sound that exceeds duration limit fails."""
        owner, server, soundboard, servers = server_with_owner

        with pytest.raises(SoundTooLongError) as exc_info:
            soundboard.upload_sound(
                user_id=owner.id,
                server_id=server.id,
                name="long_sound",
                format=SoundFormat.MP3,
                url="https://cdn.example.com/sounds/long.mp3",
                size=100000,
                duration_seconds=30.0,
            )

        assert exc_info.value.max_duration == 5


class TestGetSound:
    """Tests for getting sounds."""

    def test_get_sound_success(self, server_with_sound):
        """Test getting a sound successfully."""
        owner, server, sound, soundboard, servers = server_with_sound

        retrieved = soundboard.get_sound(sound.id, owner.id)

        assert retrieved is not None
        assert retrieved.id == sound.id
        assert retrieved.name == sound.name

    def test_get_sound_nonexistent(self, server_with_owner):
        """Test getting nonexistent sound returns None."""
        owner, server, soundboard, servers = server_with_owner

        result = soundboard.get_sound(999999999, owner.id)
        assert result is None

    def test_get_server_sounds(self, server_with_sound):
        """Test getting all sounds for a server."""
        owner, server, sound, soundboard, servers = server_with_sound

        sounds = soundboard.get_server_sounds(owner.id, server.id)

        assert len(sounds) >= 1
        sound_ids = [s.id for s in sounds]
        assert sound.id in sound_ids


class TestDeleteSound:
    """Tests for deleting sounds."""

    def test_delete_sound_success(self, db_and_modules):
        """Test deleting a sound successfully."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"del_sb_owner_{unique_id}",
            email=f"del_sb_owner_{unique_id}@example.com",
            password="TestPass123!",
        )
        server = servers.create_server(owner.id, f"Del Sound Server {unique_id}")

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="to_delete",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/delete.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        result = soundboard.delete_sound(owner.id, sound.id)
        assert result is True

        retrieved = soundboard.get_sound(sound.id, owner.id)
        assert retrieved is None

    def test_delete_sound_nonexistent_fails(self, server_with_owner):
        """Test deleting nonexistent sound fails."""
        owner, server, soundboard, servers = server_with_owner

        with pytest.raises(SoundNotFoundError):
            soundboard.delete_sound(owner.id, 999999999)
