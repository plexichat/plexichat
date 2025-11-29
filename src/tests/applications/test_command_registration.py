"""
Tests for command registration.
"""

import pytest

from src.core.applications import CommandType


@pytest.mark.applications
@pytest.mark.integration
class TestCommandRegistration:
    """Tests for registering commands."""

    def test_register_basic_command(self, modules, test_application):
        """Test registering a basic slash command."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="ping",
            description="Check bot latency",
        )

        assert cmd.id is not None
        assert cmd.application_id == app.id
        assert cmd.name == "ping"
        assert cmd.description == "Check bot latency"
        assert cmd.command_type == CommandType.CHAT_INPUT

    def test_register_guild_command(self, modules, test_application, test_server):
        """Test registering a guild-specific command."""
        app, owner = test_application
        server, server_owner = test_server

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="serverinfo",
            description="Get server information",
            server_id=server.id,
        )

        assert cmd.server_id == server.id

    def test_register_user_context_command(self, modules, test_application):
        """Test registering a user context menu command."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="Get User Info",
            description="",
            command_type=CommandType.USER,
        )

        assert cmd.command_type == CommandType.USER

    def test_register_message_context_command(self, modules, test_application):
        """Test registering a message context menu command."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="Report Message",
            description="",
            command_type=CommandType.MESSAGE,
        )

        assert cmd.command_type == CommandType.MESSAGE

    def test_register_command_with_permissions(self, modules, test_application):
        """Test registering command with default permissions."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="ban",
            description="Ban a user",
            default_member_permissions="4",
            dm_permission=False,
        )

        assert cmd.default_member_permissions == "4"
        assert cmd.dm_permission is False

    def test_register_nsfw_command(self, modules, test_application):
        """Test registering NSFW command."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="nsfw",
            description="NSFW content",
            nsfw=True,
        )

        assert cmd.nsfw is True

    def test_register_duplicate_command_fails(self, modules, test_application):
        """Test that duplicate command names fail."""
        app, owner = test_application

        modules.applications.register_command(
            application_id=app.id,
            name="unique",
            description="First command",
        )

        with pytest.raises(modules.applications.CommandValidationError):
            modules.applications.register_command(
                application_id=app.id,
                name="unique",
                description="Duplicate command",
            )


@pytest.mark.applications
@pytest.mark.integration
class TestCommandUpdate:
    """Tests for updating commands."""

    def test_update_command_description(self, modules, test_command):
        """Test updating command description."""
        cmd, app = test_command

        updated = modules.applications.update_command(
            command_id=cmd.id,
            description="Updated description",
        )

        assert updated.description == "Updated description"

    def test_update_command_name(self, modules, test_command):
        """Test updating command name."""
        cmd, app = test_command

        updated = modules.applications.update_command(
            command_id=cmd.id,
            name="newname",
        )

        assert updated.name == "newname"

    def test_update_command_increments_version(self, modules, test_command):
        """Test that updating command increments version."""
        cmd, app = test_command
        original_version = cmd.version

        updated = modules.applications.update_command(
            command_id=cmd.id,
            description="New description",
        )

        assert updated.version == original_version + 1


@pytest.mark.applications
@pytest.mark.integration
class TestCommandDelete:
    """Tests for deleting commands."""

    def test_delete_command(self, modules, command_factory):
        """Test deleting a command."""
        cmd, app = command_factory.create()

        result = modules.applications.delete_command(cmd.id)

        assert result is True

        commands = modules.applications.get_commands(app.id)
        cmd_ids = [c.id for c in commands]
        assert cmd.id not in cmd_ids

    def test_delete_nonexistent_command(self, modules):
        """Test deleting non-existent command."""
        with pytest.raises(modules.applications.CommandNotFoundError):
            modules.applications.delete_command(999999999)


@pytest.mark.applications
@pytest.mark.integration
class TestCommandRetrieval:
    """Tests for retrieving commands."""

    def test_get_global_commands(self, modules, test_application, command_factory):
        """Test getting global commands."""
        app, owner = test_application

        command_factory.create(application=app, name="cmd1")
        command_factory.create(application=app, name="cmd2")

        commands = modules.applications.get_commands(app.id)

        assert len(commands) >= 2
        names = [c.name for c in commands]
        assert "cmd1" in names
        assert "cmd2" in names

    def test_get_guild_commands(self, modules, test_application, test_server, command_factory):
        """Test getting guild-specific commands."""
        app, owner = test_application
        server, server_owner = test_server

        command_factory.create(application=app, name="global")
        command_factory.create(application=app, name="guild", server_id=server.id)

        guild_commands = modules.applications.get_commands(
            app.id, server_id=server.id, include_global=False
        )

        names = [c.name for c in guild_commands]
        assert "guild" in names
        assert "global" not in names

    def test_get_commands_includes_global(self, modules, test_application, test_server, command_factory):
        """Test getting guild commands includes global by default."""
        app, owner = test_application
        server, server_owner = test_server

        command_factory.create(application=app, name="global")
        command_factory.create(application=app, name="guild", server_id=server.id)

        all_commands = modules.applications.get_commands(app.id, server_id=server.id)

        names = [c.name for c in all_commands]
        assert "guild" in names
        assert "global" in names
