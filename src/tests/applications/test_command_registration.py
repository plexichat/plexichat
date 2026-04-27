"""Tests for application command registration."""

import pytest

from src.core.applications.models import CommandType, CommandOptionType
from src.core.applications.exceptions import (
    CommandValidationError,
    CommandNotFoundError,
)


@pytest.mark.applications
class TestCommandRegistration:
    """Tests for registering, updating, and deleting application commands."""

    def test_register_chat_input_command(self, app_manager, test_user):
        """Test registering a chat input command."""
        app = app_manager.create_application(owner_id=test_user.id, name="Cmd App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="hello",
            description="Says hello",
            command_type=CommandType.CHAT_INPUT,
        )
        assert cmd.name == "hello"
        assert cmd.description == "Says hello"
        assert cmd.command_type == CommandType.CHAT_INPUT

    def test_register_user_command(self, app_manager, test_user):
        """Test registering a user context command."""
        app = app_manager.create_application(owner_id=test_user.id, name="User Cmd App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="Report User",
            description="Reports a user",
            command_type=CommandType.USER,
        )
        assert cmd.command_type == CommandType.USER

    def test_register_message_command(self, app_manager, test_user):
        """Test registering a message context command."""
        app = app_manager.create_application(owner_id=test_user.id, name="Msg Cmd App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="Pin Message",
            description="Pins a message",
            command_type=CommandType.MESSAGE,
        )
        assert cmd.command_type == CommandType.MESSAGE

    def test_register_command_with_options(self, app_manager, test_user):
        """Test registering a command with options."""
        app = app_manager.create_application(owner_id=test_user.id, name="Opts App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="greet",
            description="Greets someone",
            options=[
                {
                    "name": "user",
                    "description": "User to greet",
                    "type": CommandOptionType.USER.value,
                    "required": True,
                },
                {
                    "name": "enthusiasm",
                    "description": "Enthusiasm level",
                    "type": CommandOptionType.INTEGER.value,
                    "required": False,
                    "min_value": 1,
                    "max_value": 10,
                },
            ],
        )
        assert cmd.name == "greet"
        assert len(cmd.options) == 2

    def test_get_commands_for_application(self, app_manager, test_user):
        """Test getting all commands for an application."""
        app = app_manager.create_application(
            owner_id=test_user.id, name="Multi Cmd App"
        )
        app_manager.register_command(
            application_id=app.id, name="cmd1", description="First"
        )
        app_manager.register_command(
            application_id=app.id, name="cmd2", description="Second"
        )
        cmds = app_manager.get_commands(app.id)
        assert len(cmds) >= 2

    def test_update_command(self, app_manager, test_user):
        """Test updating a command."""
        app = app_manager.create_application(
            owner_id=test_user.id, name="Update Cmd App"
        )
        cmd = app_manager.register_command(
            application_id=app.id, name="oldname", description="Old desc"
        )
        updated = app_manager.update_command(
            command_id=cmd.id,
            name="newname",
            description="New desc",
        )
        assert updated.name == "newname"
        assert updated.description == "New desc"

    def test_delete_command(self, app_manager, test_user):
        """Test deleting a command."""
        app = app_manager.create_application(owner_id=test_user.id, name="Del Cmd App")
        cmd = app_manager.register_command(
            application_id=app.id, name="todelete", description="To delete"
        )
        result = app_manager.delete_command(cmd.id)
        assert result is True

    def test_register_guild_command(self, app_manager, test_user, test_server):
        """Test registering a guild-specific command."""
        server, owner = test_server
        app = app_manager.create_application(
            owner_id=test_user.id, name="Guild Cmd App"
        )
        cmd = app_manager.register_command(
            application_id=app.id,
            name="guildonly",
            description="Guild only command",
            server_id=server.id,
        )
        assert cmd.server_id == server.id
