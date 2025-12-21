"""
Tests for sound cooldowns.
"""

import pytest
import uuid
from src.core.soundboard import SoundFormat, SoundCooldownError


class TestSoundCooldowns:
    """Tests for sound cooldown functionality."""

    def test_cooldown_after_play(self, db_and_modules):
        """Test sound goes on cooldown after playing."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"cd_owner_{unique_id}",
            email=f"cd_owner_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers.create_server(owner.id, f"Cooldown Server {unique_id}")

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-test",
            channel_type=servers.ChannelType.VOICE
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="cooldown_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/cooldown.mp3",
            size=100000,
            duration_seconds=2.0
        )

        soundboard.play_sound(owner.id, sound.id, voice_channel.id)

        with pytest.raises(SoundCooldownError) as exc_info:
            soundboard.play_sound(owner.id, sound.id, voice_channel.id)

        assert exc_info.value.remaining_seconds > 0

    def test_different_sounds_no_shared_cooldown(self, db_and_modules):
        """Test different sounds have independent cooldowns."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"ind_cd_owner_{unique_id}",
            email=f"ind_cd_owner_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers.create_server(owner.id, f"Ind CD Server {unique_id}")

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-ind",
            channel_type=servers.ChannelType.VOICE
        )

        sound1 = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="sound_one",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/one.mp3",
            size=100000,
            duration_seconds=2.0
        )

        sound2 = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="sound_two",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/two.mp3",
            size=100000,
            duration_seconds=2.0
        )

        soundboard.play_sound(owner.id, sound1.id, voice_channel.id)

        playback = soundboard.play_sound(owner.id, sound2.id, voice_channel.id)
        assert playback is not None

    def test_different_users_no_shared_cooldown(self, db_and_modules):
        """Test different users have independent cooldowns."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"user_cd_owner_{unique_id}",
            email=f"user_cd_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        member = auth.register(
            username=f"user_cd_member_{unique_id}",
            email=f"user_cd_member_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers.create_server(owner.id, f"User CD Server {unique_id}")
        servers.add_member(server.id, member.id)

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-users",
            channel_type=servers.ChannelType.VOICE
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="shared_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/shared.mp3",
            size=100000,
            duration_seconds=2.0
        )

        soundboard.play_sound(owner.id, sound.id, voice_channel.id)

        playback = soundboard.play_sound(member.id, sound.id, voice_channel.id)
        assert playback is not None
