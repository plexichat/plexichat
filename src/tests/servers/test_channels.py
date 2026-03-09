"""
Tests for channel operations.
"""

import pytest


class TestCreateChannel:
    """Tests for channel creation."""

    def test_create_text_channel(self, fresh_server):
        """Test creating a text channel."""
        server, owner, servers = fresh_server

        channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="test-channel",
            channel_type=servers.ChannelType.TEXT,
        )

        assert channel is not None
        assert channel.name == "test-channel"
        assert channel.channel_type == servers.ChannelType.TEXT

    def test_create_channel_with_topic(self, fresh_server):
        """Test creating channel with topic."""
        server, owner, servers = fresh_server

        channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="announcements",
            topic="Important announcements",
        )

        assert channel.topic == "Important announcements"

    def test_create_channel_with_category(self, server_with_channels):
        """Test creating channel in a category."""
        server, owner, _, _, _, _, _, _, category, servers = server_with_channels

        channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="new-channel",
            category_id=category.id,
        )

        assert channel.category_id == category.id

    def test_create_nsfw_channel(self, fresh_server):
        """Test creating NSFW channel."""
        server, owner, servers = fresh_server

        channel = servers.create_channel(
            user_id=owner.id, server_id=server.id, name="nsfw-channel", nsfw=True
        )

        assert channel.nsfw is True

    def test_create_channel_with_slowmode(self, fresh_server):
        """Test creating channel with slowmode."""
        server, owner, servers = fresh_server

        channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="slow-channel",
            slowmode_seconds=30,
        )

        assert channel.slowmode_seconds == 30

    def test_create_channel_normalizes_name(self, fresh_server):
        """Test that channel name is normalized."""
        server, owner, servers = fresh_server

        channel = servers.create_channel(
            user_id=owner.id, server_id=server.id, name="My Channel Name"
        )

        assert channel.name == "my-channel-name"

    def test_create_channel_empty_name_fails(self, fresh_server):
        """Test that empty name fails."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.InvalidChannelNameError):
            servers.create_channel(user_id=owner.id, server_id=server.id, name="")

    def test_create_channel_by_non_admin_fails(self, server_with_members):
        """Test that non-admin cannot create channel."""
        server, _, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_channel(
                user_id=member_user.id, server_id=server.id, name="hacked-channel"
            )


class TestCreateCategory:
    """Tests for category creation."""

    def test_create_category(self, fresh_server):
        """Test creating a category."""
        server, owner, servers = fresh_server

        category = servers.create_category(
            user_id=owner.id, server_id=server.id, name="Text Channels"
        )

        assert category is not None
        assert category.name == "Text Channels"

    def test_create_category_empty_name_fails(self, fresh_server):
        """Test that empty category name fails."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.InvalidChannelNameError):
            servers.create_category(user_id=owner.id, server_id=server.id, name="")

    def test_create_category_by_non_admin_fails(self, server_with_members):
        """Test that non-admin cannot create categories."""
        server, _, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_category(user_id=member_user.id, server_id=server.id, name="ops")


class TestDeleteCategory:
    """Tests for category deletion."""

    def test_delete_category_by_non_admin_fails(self, server_with_channels):
        """Test that non-admin cannot delete categories."""
        server, _, _, member_user, _, _, _, _, category, servers = server_with_channels

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_category(member_user.id, category.id)


class TestGetChannel:
    """Tests for getting channel info."""

    def test_get_channel_as_member(self, server_with_channels):
        """Test getting channel as member."""
        server, _, _, member_user, _, general, _, _, _, servers = server_with_channels

        channel = servers.get_channel(general.id, member_user.id)

        assert channel is not None
        assert channel.id == general.id

    def test_get_channel_as_non_member(self, server_with_channels):
        """Test getting channel as non-member returns None."""
        server, _, _, _, outsider, general, _, _, _, servers = server_with_channels

        channel = servers.get_channel(general.id, outsider.id)

        assert channel is None

    def test_get_channel_nonexistent(self, users):
        """Test getting nonexistent channel."""
        owner, _, _, _, servers = users

        channel = servers.get_channel(999999999, owner.id)

        assert channel is None


class TestGetChannels:
    """Tests for listing channels."""

    def test_get_channels_returns_server_channels(self, server_with_channels):
        """Test getting all channels in server."""
        server, owner, _, _, _, general, announcements, private, _, servers = (
            server_with_channels
        )

        channels = servers.get_channels(owner.id, server.id)

        assert len(channels) >= 3
        channel_ids = [c.id for c in channels]
        assert general.id in channel_ids
        assert announcements.id in channel_ids
        assert private.id in channel_ids

    def test_get_channels_filter_by_type(self, server_with_channels):
        """Test filtering channels by type."""
        server, owner, _, _, _, _, _, _, _, servers = server_with_channels

        text_channels = servers.get_channels(
            owner.id, server.id, channel_type=servers.ChannelType.TEXT
        )

        assert all(c.channel_type == servers.ChannelType.TEXT for c in text_channels)

    def test_get_channels_ordered_by_position(self, server_with_channels):
        """Test that channels are ordered by position."""
        server, owner, _, _, _, _, _, _, _, servers = server_with_channels

        channels = servers.get_channels(owner.id, server.id)

        positions = [c.position for c in channels]
        assert positions == sorted(positions)


class TestUpdateChannel:
    """Tests for updating channels."""

    def test_update_channel_name(self, server_with_channels):
        """Test updating channel name."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        updated = servers.update_channel(
            user_id=owner.id, channel_id=general.id, name="main"
        )

        assert updated.name == "main"

    def test_update_channel_topic(self, server_with_channels):
        """Test updating channel topic."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        updated = servers.update_channel(
            user_id=owner.id, channel_id=general.id, topic="Welcome to the server!"
        )

        assert updated.topic == "Welcome to the server!"

    def test_update_channel_nsfw(self, server_with_channels):
        """Test updating channel NSFW flag."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        updated = servers.update_channel(
            user_id=owner.id, channel_id=general.id, nsfw=True
        )

        assert updated.nsfw is True

    def test_update_channel_by_non_admin_fails(self, server_with_channels):
        """Test that non-admin cannot update channel."""
        server, _, _, member_user, _, general, _, _, _, servers = server_with_channels

        with pytest.raises(servers.PermissionDeniedError):
            servers.update_channel(
                user_id=member_user.id, channel_id=general.id, name="hacked"
            )


class TestDeleteChannel:
    """Tests for deleting channels."""

    def test_delete_channel(self, server_with_channels):
        """Test deleting a channel."""
        server, owner, _, _, _, _, announcements, _, _, servers = server_with_channels

        result = servers.delete_channel(owner.id, announcements.id)

        assert result is True
        assert servers.get_channel(announcements.id, owner.id) is None

    def test_delete_channel_by_non_admin_fails(self, server_with_channels):
        """Test that non-admin cannot delete channel."""
        server, _, _, member_user, _, general, _, _, _, servers = server_with_channels

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_channel(member_user.id, general.id)


class TestMoveChannel:
    """Tests for moving channels."""

    def test_move_channel(self, server_with_channels):
        """Test moving a channel."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        updated = servers.move_channel(owner.id, general.id, position=10)

        assert updated.position == 10

    def test_move_channel_by_non_admin_fails(self, server_with_channels):
        """Test that non-admin cannot move channels."""
        server, _, _, member_user, _, general, _, _, _, servers = server_with_channels

        with pytest.raises(servers.PermissionDeniedError):
            servers.move_channel(member_user.id, general.id, position=10)
