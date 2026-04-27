"""Tests for application command options validation."""

import pytest

from src.core.applications.models import CommandType, CommandOptionType
from src.core.applications.exceptions import (
    CommandValidationError,
    CommandOptionLimitError,
)


@pytest.mark.applications
class TestCommandOptions:
    """Tests for command option validation and limits."""

    def test_string_option(self, app_manager, test_user):
        """Test registering a command with string option."""
        app = app_manager.create_application(owner_id=test_user.id, name="Str Opt App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="search",
            description="Search for something",
            options=[
                {
                    "name": "query",
                    "description": "Search query",
                    "type": CommandOptionType.STRING.value,
                    "required": True,
                }
            ],
        )
        assert len(cmd.options) == 1

    def test_integer_option_with_bounds(self, app_manager, test_user):
        """Test integer option with min/max values."""
        app = app_manager.create_application(owner_id=test_user.id, name="Int Opt App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="roll",
            description="Roll dice",
            options=[
                {
                    "name": "sides",
                    "description": "Number of sides",
                    "type": CommandOptionType.INTEGER.value,
                    "required": True,
                    "min_value": 2,
                    "max_value": 100,
                }
            ],
        )
        assert len(cmd.options) == 1

    def test_boolean_option(self, app_manager, test_user):
        """Test registering a command with boolean option."""
        app = app_manager.create_application(owner_id=test_user.id, name="Bool Opt App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="toggle",
            description="Toggle feature",
            options=[
                {
                    "name": "enabled",
                    "description": "Whether enabled",
                    "type": CommandOptionType.BOOLEAN.value,
                    "required": True,
                }
            ],
        )
        assert len(cmd.options) == 1

    def test_channel_option(self, app_manager, test_user):
        """Test registering a command with channel option."""
        app = app_manager.create_application(owner_id=test_user.id, name="Ch Opt App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="move",
            description="Move to channel",
            options=[
                {
                    "name": "target",
                    "description": "Target channel",
                    "type": CommandOptionType.CHANNEL.value,
                    "required": True,
                }
            ],
        )
        assert len(cmd.options) == 1

    def test_multiple_options(self, app_manager, test_user):
        """Test registering a command with multiple options."""
        app = app_manager.create_application(
            owner_id=test_user.id, name="Multi Opt App"
        )
        cmd = app_manager.register_command(
            application_id=app.id,
            name="remind",
            description="Set a reminder",
            options=[
                {
                    "name": "message",
                    "description": "Reminder text",
                    "type": CommandOptionType.STRING.value,
                    "required": True,
                },
                {
                    "name": "minutes",
                    "description": "Minutes from now",
                    "type": CommandOptionType.INTEGER.value,
                    "required": True,
                    "min_value": 1,
                },
                {
                    "name": "repeat",
                    "description": "Repeat daily",
                    "type": CommandOptionType.BOOLEAN.value,
                    "required": False,
                },
            ],
        )
        assert len(cmd.options) == 3

    def test_option_with_choices(self, app_manager, test_user):
        """Test registering a command with choices."""
        app = app_manager.create_application(owner_id=test_user.id, name="Choice App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="color",
            description="Pick a color",
            options=[
                {
                    "name": "color",
                    "description": "Color name",
                    "type": CommandOptionType.STRING.value,
                    "required": True,
                    "choices": [
                        {"name": "Red", "value": "red"},
                        {"name": "Blue", "value": "blue"},
                        {"name": "Green", "value": "green"},
                    ],
                }
            ],
        )
        assert len(cmd.options) == 1

    def test_subcommand_option(self, app_manager, test_user):
        """Test registering a command with subcommand."""
        app = app_manager.create_application(owner_id=test_user.id, name="Subcmd App")
        cmd = app_manager.register_command(
            application_id=app.id,
            name="config",
            description="Configure settings",
            options=[
                {
                    "name": "set",
                    "description": "Set a value",
                    "type": CommandOptionType.SUB_COMMAND.value,
                    "options": [
                        {
                            "name": "key",
                            "description": "Setting key",
                            "type": CommandOptionType.STRING.value,
                            "required": True,
                        }
                    ],
                }
            ],
        )
        assert len(cmd.options) == 1
