"""
Integration tests for the applications module.

Tests complete workflows involving multiple components.
"""

import pytest

from src.core.applications import (
    CommandType,
    CommandOptionType,
    InteractionType,
    create_message_response,
    create_deferred_response,
    create_autocomplete_response,
)


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationWorkflow:
    """Tests for complete application workflows."""

    def test_create_application_with_bot_and_commands(self, modules, user_pool):
        """Test creating application, adding bot, and registering commands."""
        owner = user_pool.get_user()

        app = modules.applications.create_application(
            owner_id=owner.id,
            name="Complete Bot",
            description="A fully featured bot",
            redirect_uris=["https://example.com/callback"],
        )

        bot_info = modules.applications.create_bot_for_application(
            user_id=owner.id,
            application_id=app.id,
            permissions={"messages.send": True, "messages.read": True},
        )

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="greet",
            description="Greet a user",
            options=[
                {
                    "name": "user",
                    "description": "User to greet",
                    "type": CommandOptionType.USER,
                    "required": True,
                },
                {
                    "name": "message",
                    "description": "Custom greeting",
                    "type": CommandOptionType.STRING,
                    "required": False,
                },
            ],
        )

        assert app.id is not None
        assert bot_info["bot_id"] is not None
        assert cmd.id is not None

        updated_app = modules.applications.get_application(app.id)
        assert updated_app.bot_id == bot_info["bot_id"]

    def test_command_interaction_workflow(self, modules, app_factory, user_pool):
        """Test complete command interaction workflow."""
        app, owner = app_factory.create()
        user = user_pool.get_user()

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="ping",
            description="Check latency",
        )

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={
                "id": cmd.id,
                "name": cmd.name,
                "type": cmd.command_type.value,
            },
        )

        response = create_message_response(content="Pong! Latency: 42ms")
        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True

    def test_autocomplete_workflow(self, modules, app_factory, user_pool):
        """Test autocomplete interaction workflow."""
        app, owner = app_factory.create()
        user = user_pool.get_user()

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="search",
            description="Search for items",
            options=[
                {
                    "name": "query",
                    "description": "Search query",
                    "type": CommandOptionType.STRING,
                    "autocomplete": True,
                },
            ],
        )

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE,
            user_id=user.id,
            data={
                "id": cmd.id,
                "name": cmd.name,
                "type": cmd.command_type.value,
                "options": [{"name": "query", "value": "test", "focused": True}],
            },
        )

        response = create_autocomplete_response(
            choices=[
                {"name": "Test Result 1", "value": "result1"},
                {"name": "Test Result 2", "value": "result2"},
            ]
        )
        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True

    def test_deferred_response_workflow(self, modules, app_factory, user_pool):
        """Test deferred response workflow."""
        app, owner = app_factory.create()
        user = user_pool.get_user()

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="slowcmd",
            description="A slow command",
        )

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={
                "id": cmd.id,
                "name": cmd.name,
                "type": cmd.command_type.value,
            },
        )

        response = create_deferred_response()
        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True

    def test_server_installation_workflow(
        self, modules, app_factory, test_server, user_pool
    ):
        """Test application installation on server."""
        app, owner = app_factory.create()
        server, server_owner = test_server
        installer = user_pool.get_user()

        modules.applications.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=installer.id,
            permissions="8",
            scopes=["bot", "applications.commands"],
        )

        modules.applications.register_command(
            application_id=app.id,
            name="serveronly",
            description="Server-specific command",
            server_id=server.id,
        )

        commands = modules.applications.get_commands(app.id, server_id=server.id)
        cmd_names = [c.name for c in commands]
        assert "serveronly" in cmd_names

        modules.applications.uninstall_application(app.id, server.id, installer.id)

        installations = modules.applications.get_installations(
            application_id=app.id,
            server_id=server.id,
        )
        assert len(installations) == 0


@pytest.mark.applications
@pytest.mark.integration
class TestContextMenuCommands:
    """Tests for context menu command workflows."""

    def test_user_context_menu_workflow(self, modules, app_factory, user_pool):
        """Test user context menu command workflow."""
        app, owner = app_factory.create()
        user = user_pool.get_user()
        target_user = user_pool.get_user()

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="Get User Info",
            description="",
            command_type=CommandType.USER,
        )

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={
                "id": cmd.id,
                "name": cmd.name,
                "type": cmd.command_type.value,
                "target_id": target_user.id,
            },
        )

        response = create_message_response(
            content=f"User ID: {target_user.id}",
            ephemeral=True,
        )
        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True

    def test_message_context_menu_workflow(self, modules, app_factory, user_pool):
        """Test message context menu command workflow."""
        app, owner = app_factory.create()
        user = user_pool.get_user()

        cmd = modules.applications.register_command(
            application_id=app.id,
            name="Report Message",
            description="",
            command_type=CommandType.MESSAGE,
        )

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={
                "id": cmd.id,
                "name": cmd.name,
                "type": cmd.command_type.value,
                "target_id": 123456789,
            },
            message_id=123456789,
        )

        response = create_message_response(
            content="Message reported. Thank you!",
            ephemeral=True,
        )
        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True
