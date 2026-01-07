"""
Tests for command options and validation.
"""

import pytest

from src.core.applications import CommandOptionType


@pytest.mark.applications
@pytest.mark.integration
class TestCommandOptions:
    """Tests for command options."""

    def test_register_command_with_string_option(self, modules, test_application):
        """Test registering command with string option."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="echo",
            description="Echo a message",
            options=[
                {
                    "name": "message",
                    "description": "Message to echo",
                    "type": CommandOptionType.STRING,
                    "required": True,
                }
            ],
        )

        assert len(cmd.options) == 1
        assert cmd.options[0].name == "message"
        assert cmd.options[0].option_type == CommandOptionType.STRING
        assert cmd.options[0].required is True

    def test_register_command_with_integer_option(self, modules, test_application):
        """Test registering command with integer option."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="roll",
            description="Roll dice",
            options=[
                {
                    "name": "sides",
                    "description": "Number of sides",
                    "type": CommandOptionType.INTEGER,
                    "required": False,
                    "min_value": 2,
                    "max_value": 100,
                }
            ],
        )

        assert cmd.options[0].option_type == CommandOptionType.INTEGER
        assert cmd.options[0].min_value == 2
        assert cmd.options[0].max_value == 100

    def test_register_command_with_boolean_option(self, modules, test_application):
        """Test registering command with boolean option."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="toggle",
            description="Toggle setting",
            options=[
                {
                    "name": "enabled",
                    "description": "Enable or disable",
                    "type": CommandOptionType.BOOLEAN,
                }
            ],
        )

        assert cmd.options[0].option_type == CommandOptionType.BOOLEAN

    def test_register_command_with_user_option(self, modules, test_application):
        """Test registering command with user option."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="info",
            description="Get user info",
            options=[
                {
                    "name": "user",
                    "description": "User to get info for",
                    "type": CommandOptionType.USER,
                }
            ],
        )

        assert cmd.options[0].option_type == CommandOptionType.USER

    def test_register_command_with_channel_option(self, modules, test_application):
        """Test registering command with channel option."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="move",
            description="Move to channel",
            options=[
                {
                    "name": "channel",
                    "description": "Target channel",
                    "type": CommandOptionType.CHANNEL,
                    "channel_types": [0, 2],
                }
            ],
        )

        assert cmd.options[0].option_type == CommandOptionType.CHANNEL
        assert cmd.options[0].channel_types == [0, 2]

    def test_register_command_with_choices(self, modules, test_application):
        """Test registering command with choices."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="color",
            description="Choose a color",
            options=[
                {
                    "name": "color",
                    "description": "Color to choose",
                    "type": CommandOptionType.STRING,
                    "choices": [
                        {"name": "Red", "value": "red"},
                        {"name": "Green", "value": "green"},
                        {"name": "Blue", "value": "blue"},
                    ],
                }
            ],
        )

        assert len(cmd.options[0].choices) == 3

    def test_register_command_with_autocomplete(self, modules, test_application):
        """Test registering command with autocomplete."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="search",
            description="Search for something",
            options=[
                {
                    "name": "query",
                    "description": "Search query",
                    "type": CommandOptionType.STRING,
                    "autocomplete": True,
                }
            ],
        )

        assert cmd.options[0].autocomplete is True

    def test_register_command_with_multiple_options(self, modules, test_application):
        """Test registering command with multiple options."""
        app, owner = test_application

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="complex",
            description="Complex command",
            options=[
                {
                    "name": "text",
                    "description": "Text input",
                    "type": CommandOptionType.STRING,
                    "required": True,
                },
                {
                    "name": "count",
                    "description": "Count",
                    "type": CommandOptionType.INTEGER,
                    "required": True,
                },
                {
                    "name": "user",
                    "description": "Target user",
                    "type": CommandOptionType.USER,
                    "required": False,
                },
            ],
        )

        assert len(cmd.options) == 3


@pytest.mark.applications
@pytest.mark.integration
class TestCommandOptionValidation:
    """Tests for command option validation."""

    def test_validate_valid_option(self, modules):
        """Test validating a valid option."""
        valid, issues = modules.applications.validate_option(
            {
                "name": "test",
                "description": "Test option",
                "type": CommandOptionType.STRING,
            }
        )

        assert valid is True
        assert len(issues) == 0

    def test_validate_option_missing_name(self, modules):
        """Test validating option without name."""
        valid, issues = modules.applications.validate_option(
            {
                "description": "Test option",
                "type": CommandOptionType.STRING,
            }
        )

        assert valid is False
        assert any("name" in issue.lower() for issue in issues)

    def test_validate_option_missing_description(self, modules):
        """Test validating option without description."""
        valid, issues = modules.applications.validate_option(
            {
                "name": "test",
                "type": CommandOptionType.STRING,
            }
        )

        assert valid is False
        assert any("description" in issue.lower() for issue in issues)

    def test_validate_option_invalid_name(self, modules):
        """Test validating option with invalid name."""
        valid, issues = modules.applications.validate_option(
            {
                "name": "Invalid Name!",
                "description": "Test option",
                "type": CommandOptionType.STRING,
            }
        )

        assert valid is False

    def test_validate_option_name_too_long(self, modules):
        """Test validating option with name too long."""
        valid, issues = modules.applications.validate_option(
            {
                "name": "a" * 50,
                "description": "Test option",
                "type": CommandOptionType.STRING,
            }
        )

        assert valid is False

    def test_validate_options_list(self, modules):
        """Test validating list of options."""
        valid, issues = modules.applications.validate_options(
            [
                {
                    "name": "opt1",
                    "description": "Option 1",
                    "type": CommandOptionType.STRING,
                    "required": True,
                },
                {
                    "name": "opt2",
                    "description": "Option 2",
                    "type": CommandOptionType.INTEGER,
                    "required": False,
                },
            ]
        )

        assert valid is True

    def test_validate_options_duplicate_names(self, modules):
        """Test validating options with duplicate names."""
        valid, issues = modules.applications.validate_options(
            [
                {
                    "name": "same",
                    "description": "Option 1",
                    "type": CommandOptionType.STRING,
                },
                {
                    "name": "same",
                    "description": "Option 2",
                    "type": CommandOptionType.INTEGER,
                },
            ]
        )

        assert valid is False
        assert any("duplicate" in issue.lower() for issue in issues)

    def test_validate_options_required_order(self, modules):
        """Test that required options must come before optional."""
        valid, issues = modules.applications.validate_options(
            [
                {
                    "name": "optional",
                    "description": "Optional first",
                    "type": CommandOptionType.STRING,
                    "required": False,
                },
                {
                    "name": "required",
                    "description": "Required after optional",
                    "type": CommandOptionType.STRING,
                    "required": True,
                },
            ]
        )

        assert valid is False


@pytest.mark.applications
@pytest.mark.integration
class TestCommandValidation:
    """Tests for command validation."""

    def test_validate_valid_command(self, modules):
        """Test validating a valid command."""
        valid, issues = modules.applications.validate_command(
            {
                "name": "test",
                "description": "Test command",
            }
        )

        assert valid is True

    def test_validate_command_name_uppercase(self, modules):
        """Test that command names must be lowercase."""
        valid, issues = modules.applications.validate_command_name("TestCommand")

        assert valid is False

    def test_validate_command_name_special_chars(self, modules):
        """Test command name with special characters."""
        valid, issues = modules.applications.validate_command_name("test!")

        assert valid is False

    def test_validate_command_description_too_long(self, modules):
        """Test command description too long."""
        from src.core.applications import CommandType

        valid, issues = modules.applications.validate_command_description(
            "a" * 200,
            CommandType.CHAT_INPUT,
        )

        assert valid is False
