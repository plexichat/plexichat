"""
Tests for voice channel limits and settings.
"""

import pytest


class TestUserLimit:
    """Tests for user limit functionality."""

    def test_set_user_limit(self, server_with_voice):
        """Test setting user limit."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.set_user_limit(owner.id, voice_channel.id, 5)

        assert channel.user_limit == 5

    def test_set_user_limit_zero_unlimited(self, server_with_voice):
        """Test setting user limit to 0 means unlimited."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.set_user_limit(owner.id, voice_channel.id, 5)
        channel = voice.set_user_limit(owner.id, voice_channel.id, 0)

        assert channel.user_limit == 0

    def test_join_full_channel_fails(self, server_with_voice):
        """Test joining a full channel fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.set_user_limit(owner.id, voice_channel.id, 1)

        voice.join_channel(owner.id, voice_channel.id)

        with pytest.raises(voice.ChannelFullError) as exc_info:
            voice.join_channel(member1.id, voice_channel.id)

        assert exc_info.value.limit == 1
        assert exc_info.value.current == 1

    def test_moderator_can_join_full_channel(self, server_with_moderator):
        """Test moderator with move_members can join full channel."""
        owner, moderator, member, server, voice_channel, stage_channel, servers, voice = server_with_moderator

        voice.set_user_limit(owner.id, voice_channel.id, 1)

        voice.join_channel(member.id, voice_channel.id)

        state = voice.join_channel(moderator.id, voice_channel.id)

        assert state is not None
        assert state.channel_id == voice_channel.id

    def test_set_user_limit_without_permission(self, server_with_voice):
        """Test setting user limit without permission fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.PermissionDeniedError):
            voice.set_user_limit(member1.id, voice_channel.id, 5)

    def test_set_user_limit_negative_becomes_zero(self, server_with_voice):
        """Test setting negative user limit becomes 0."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.set_user_limit(owner.id, voice_channel.id, -5)

        assert channel.user_limit == 0


class TestBitrate:
    """Tests for bitrate functionality."""

    def test_set_bitrate(self, server_with_voice):
        """Test setting bitrate."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.set_bitrate(owner.id, voice_channel.id, 128000)

        assert channel.bitrate == 128000

    def test_set_bitrate_minimum(self, server_with_voice):
        """Test setting bitrate below minimum clamps to 8000."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.set_bitrate(owner.id, voice_channel.id, 1000)

        assert channel.bitrate == 8000

    def test_set_bitrate_maximum(self, server_with_voice):
        """Test setting bitrate above maximum clamps to 384000."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.set_bitrate(owner.id, voice_channel.id, 500000)

        assert channel.bitrate == 384000

    def test_set_bitrate_without_permission(self, server_with_voice):
        """Test setting bitrate without permission fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.PermissionDeniedError):
            voice.set_bitrate(member1.id, voice_channel.id, 128000)


class TestVoiceRegion:
    """Tests for voice region functionality."""

    def test_get_voice_regions(self, server_with_voice):
        """Test getting available voice regions."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        regions = voice.get_voice_regions()

        assert len(regions) > 0
        assert any(r.id == "automatic" for r in regions)
        assert any(r.id == "us-west" for r in regions)

    def test_set_voice_region(self, server_with_voice):
        """Test setting voice region."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        channel = voice.set_voice_region(owner.id, voice_channel.id, "us-west")

        assert channel.region_id == "us-west"

    def test_set_voice_region_automatic(self, server_with_voice):
        """Test setting voice region to automatic (None)."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        voice.set_voice_region(owner.id, voice_channel.id, "us-west")
        channel = voice.set_voice_region(owner.id, voice_channel.id, None)

        assert channel.region_id is None

    def test_set_invalid_voice_region(self, server_with_voice):
        """Test setting invalid voice region fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.InvalidVoiceStateError):
            voice.set_voice_region(owner.id, voice_channel.id, "invalid-region")

    def test_set_voice_region_without_permission(self, server_with_voice):
        """Test setting voice region without permission fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(voice.PermissionDeniedError):
            voice.set_voice_region(member1.id, voice_channel.id, "us-west")


class TestChannelSettings:
    """Tests for channel settings on text channels."""

    def test_set_user_limit_on_text_channel_fails(self, server_with_voice):
        """Test setting user limit on text channel fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        text_channel = servers.create_channel(
            owner.id, server.id, "text-limit",
            channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(voice.ChannelTypeError):
            voice.set_user_limit(owner.id, text_channel.id, 5)

    def test_set_bitrate_on_text_channel_fails(self, server_with_voice):
        """Test setting bitrate on text channel fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        text_channel = servers.create_channel(
            owner.id, server.id, "text-bitrate",
            channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(voice.ChannelTypeError):
            voice.set_bitrate(owner.id, text_channel.id, 128000)

    def test_set_voice_region_on_text_channel_fails(self, server_with_voice):
        """Test setting voice region on text channel fails."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        text_channel = servers.create_channel(
            owner.id, server.id, "text-region",
            channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(voice.ChannelTypeError):
            voice.set_voice_region(owner.id, text_channel.id, "us-west")
