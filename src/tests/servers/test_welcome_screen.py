"""
Tests for server welcome screen functionality.
"""

import pytest


@pytest.mark.servers
class TestWelcomeScreenCreation:
    """Tests for creating and setting welcome screens."""

    def test_set_welcome_screen(self, server_with_channels):
        """Test setting a welcome screen for a server."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        welcome_screen = servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Welcome to our server!",
            enabled=True,
        )

        assert welcome_screen is not None
        assert welcome_screen.server_id == server.id
        assert welcome_screen.description == "Welcome to our server!"
        assert welcome_screen.enabled is True

    def test_set_welcome_screen_with_channels(self, server_with_channels):
        """Test setting a welcome screen with recommended channels."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        welcome_channels = [
            {"channel_id": general.id, "description": "Start chatting here"},
            {"channel_id": announcements.id, "description": "Important updates"},
        ]

        welcome_screen = servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Welcome!",
            welcome_channels=welcome_channels,
        )

        assert welcome_screen is not None
        assert len(welcome_screen.welcome_channels) == 2

    def test_set_welcome_screen_requires_permission(self, server_with_members):
        """Test that setting welcome screen requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.set_welcome_screen(
                user_id=member_user.id,
                server_id=server.id,
                description="Unauthorized welcome",
            )

    def test_set_welcome_screen_validates_channels(self, server_with_channels):
        """Test that welcome screen validates channel IDs."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        welcome_channels = [
            {"channel_id": 999999999, "description": "Invalid channel"},
        ]

        with pytest.raises(servers.ChannelNotFoundError):
            servers.set_welcome_screen(
                user_id=owner.id,
                server_id=server.id,
                description="Welcome!",
                welcome_channels=welcome_channels,
            )


@pytest.mark.servers
class TestWelcomeScreenRetrieval:
    """Tests for retrieving welcome screens."""

    def test_get_welcome_screen(self, server_with_members):
        """Test getting a welcome screen."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Test welcome",
        )

        welcome_screen = servers.get_welcome_screen(server.id, owner.id)

        assert welcome_screen is not None
        assert welcome_screen.description == "Test welcome"

    def test_get_welcome_screen_as_member(self, server_with_members):
        """Test that members can view welcome screen."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Member visible welcome",
        )

        welcome_screen = servers.get_welcome_screen(server.id, member_user.id)

        assert welcome_screen is not None
        assert welcome_screen.description == "Member visible welcome"

    def test_get_nonexistent_welcome_screen(self, fresh_server):
        """Test getting welcome screen when none exists."""
        server, owner, servers = fresh_server

        welcome_screen = servers.get_welcome_screen(server.id, owner.id)

        assert welcome_screen is None


@pytest.mark.servers
class TestWelcomeScreenUpdate:
    """Tests for updating welcome screens."""

    def test_update_welcome_screen_description(self, server_with_members):
        """Test updating welcome screen description."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Original description",
        )

        updated = servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Updated description",
        )

        assert updated.description == "Updated description"

    def test_update_welcome_screen_enabled(self, server_with_members):
        """Test enabling/disabling welcome screen."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Toggle test",
            enabled=True,
        )

        updated = servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Toggle test",
            enabled=False,
        )

        assert updated.enabled is False

    def test_update_welcome_channels(self, server_with_channels):
        """Test updating welcome channels."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Channel update test",
            welcome_channels=[{"channel_id": general.id, "description": "General"}],
        )

        updated = servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Channel update test",
            welcome_channels=[
                {"channel_id": general.id, "description": "General"},
                {"channel_id": announcements.id, "description": "News"},
            ],
        )

        assert len(updated.welcome_channels) == 2


@pytest.mark.servers
class TestWelcomeScreenDeletion:
    """Tests for deleting welcome screens."""

    def test_delete_welcome_screen(self, server_with_members):
        """Test deleting a welcome screen."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="To delete",
        )

        result = servers.delete_welcome_screen(owner.id, server.id)
        assert result is True

        deleted = servers.get_welcome_screen(server.id, owner.id)
        assert deleted is None

    def test_delete_welcome_screen_requires_permission(self, server_with_members):
        """Test that deleting welcome screen requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Protected",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_welcome_screen(member_user.id, server.id)


@pytest.mark.servers
class TestWelcomeScreenLimits:
    """Tests for welcome screen limits."""

    def test_max_welcome_channels_limit(self, server_with_channels):
        """Test that welcome channels are limited."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        channels = []
        for i in range(10):
            ch = servers.create_channel(
                user_id=owner.id,
                server_id=server.id,
                name=f"channel-{i}",
            )
            channels.append({"channel_id": ch.id, "description": f"Channel {i}"})

        with pytest.raises(servers.OnboardingError):
            servers.set_welcome_screen(
                user_id=owner.id,
                server_id=server.id,
                description="Too many channels",
                welcome_channels=channels,
            )
