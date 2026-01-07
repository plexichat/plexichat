"""
Tests for sound playback.
"""

import pytest
import uuid
from src.core.soundboard import (
    SoundFormat,
    SoundNotFoundError,
    ChannelNotFoundError,
)


class TestPlaySound:
    """Tests for playing sounds."""

    def test_play_sound_success(self, db_and_modules):
        """Test playing a sound successfully."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"play_owner_{unique_id}",
            email=f"play_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Play Server {unique_id}")

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-play",
            channel_type=servers.ChannelType.VOICE,
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="play_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/play.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        playback = soundboard.play_sound(owner.id, sound.id, voice_channel.id)

        assert playback is not None
        assert playback.sound.id == sound.id
        assert playback.user_id == owner.id
        assert playback.channel_id == voice_channel.id
        assert playback.timestamp > 0

    def test_play_sound_tracks_usage(self, db_and_modules):
        """Test playing sound increments usage count."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"usage_owner_{unique_id}",
            email=f"usage_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Usage Server {unique_id}")

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-usage",
            channel_type=servers.ChannelType.VOICE,
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="usage_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/usage.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        initial = soundboard.get_sound(sound.id, owner.id)
        initial_count = initial.usage_count

        soundboard.play_sound(owner.id, sound.id, voice_channel.id)

        updated = soundboard.get_sound(sound.id, owner.id)
        assert updated.usage_count == initial_count + 1

    def test_play_sound_nonexistent_fails(self, db_and_modules):
        """Test playing nonexistent sound fails."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"nonex_owner_{unique_id}",
            email=f"nonex_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Nonex Server {unique_id}")

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-nonex",
            channel_type=servers.ChannelType.VOICE,
        )

        with pytest.raises(SoundNotFoundError):
            soundboard.play_sound(owner.id, 999999999, voice_channel.id)

    def test_play_sound_text_channel_fails(self, db_and_modules):
        """Test playing sound in text channel fails."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"text_owner_{unique_id}",
            email=f"text_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Text Server {unique_id}")

        text_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="text-channel",
            channel_type=servers.ChannelType.TEXT,
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="text_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/text.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        with pytest.raises(ChannelNotFoundError):
            soundboard.play_sound(owner.id, sound.id, text_channel.id)

    def test_play_sound_nonexistent_channel_fails(self, db_and_modules):
        """Test playing sound in nonexistent channel fails."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"no_chan_owner_{unique_id}",
            email=f"no_chan_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"No Chan Server {unique_id}")

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="no_chan_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/nochan.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        with pytest.raises(ChannelNotFoundError):
            soundboard.play_sound(owner.id, sound.id, 999999999)
