"""
Tests for interaction handling.
"""

import pytest

from src.core.applications import InteractionType, InteractionResponseType


@pytest.mark.applications
@pytest.mark.integration
class TestInteractionCreation:
    """Tests for creating interactions."""

    def test_create_command_interaction(self, modules, test_application, user_pool):
        """Test creating a command interaction."""
        app, owner = test_application
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={"name": "ping", "type": 1},
        )

        assert interaction.id is not None
        assert interaction.application_id == app.id
        assert interaction.user_id == user.id
        assert interaction.interaction_type == InteractionType.APPLICATION_COMMAND
        assert interaction.token is not None

    def test_create_component_interaction(self, modules, test_application, user_pool):
        """Test creating a component interaction."""
        app, owner = test_application
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.MESSAGE_COMPONENT,
            user_id=user.id,
            data={"custom_id": "btn_click", "component_type": 2},
        )

        assert interaction.interaction_type == InteractionType.MESSAGE_COMPONENT

    def test_create_autocomplete_interaction(
        self, modules, test_application, user_pool
    ):
        """Test creating an autocomplete interaction."""
        app, owner = test_application
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE,
            user_id=user.id,
            data={
                "name": "search",
                "type": 1,
                "options": [{"name": "query", "value": "test"}],
            },
        )

        assert (
            interaction.interaction_type
            == InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE
        )

    def test_create_modal_submit_interaction(
        self, modules, test_application, user_pool
    ):
        """Test creating a modal submit interaction."""
        app, owner = test_application
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.MODAL_SUBMIT,
            user_id=user.id,
            data={
                "custom_id": "feedback_modal",
                "components": [
                    {
                        "type": 1,
                        "components": [
                            {"type": 4, "custom_id": "text", "value": "feedback"}
                        ],
                    }
                ],
            },
        )

        assert interaction.interaction_type == InteractionType.MODAL_SUBMIT

    def test_create_interaction_with_server(
        self, modules, test_application, test_server, user_pool
    ):
        """Test creating interaction with server context."""
        app, owner = test_application
        server, server_owner = test_server
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={"name": "test", "type": 1},
            server_id=server.id,
            channel_id=12345,
        )

        assert interaction.server_id == server.id
        assert interaction.channel_id == 12345

    def test_create_interaction_with_locale(self, modules, test_application, user_pool):
        """Test creating interaction with locale."""
        app, owner = test_application
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=user.id,
            data={"name": "test", "type": 1},
            locale="en-US",
            server_locale="en-GB",
        )

        assert interaction.locale == "en-US"
        assert interaction.server_locale == "en-GB"


@pytest.mark.applications
@pytest.mark.integration
class TestInteractionResponses:
    """Tests for interaction responses."""

    def test_respond_with_message(self, modules, test_interaction):
        """Test responding with a message."""
        interaction, app, user = test_interaction

        response = modules.applications.create_message_response(
            content="Hello, World!",
        )

        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True

    def test_respond_with_ephemeral_message(self, modules, test_interaction):
        """Test responding with ephemeral message."""
        interaction, app, user = test_interaction

        response = modules.applications.create_message_response(
            content="Only you can see this!",
            ephemeral=True,
        )

        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True
        assert response.flags == 64

    def test_respond_with_deferred(self, modules, test_interaction):
        """Test responding with deferred response."""
        interaction, app, user = test_interaction

        response = modules.applications.create_deferred_response()

        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True
        assert (
            response.response_type
            == InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        )

    def test_respond_already_responded(self, modules, test_interaction):
        """Test that responding twice fails."""
        interaction, app, user = test_interaction

        response = modules.applications.create_message_response(content="First")
        modules.applications.create_interaction_response(interaction.token, response)

        with pytest.raises(modules.applications.InteractionAlreadyRespondedError):
            modules.applications.create_interaction_response(
                interaction.token,
                modules.applications.create_message_response(content="Second"),
            )

    def test_respond_invalid_token(self, modules):
        """Test responding with invalid token."""
        response = modules.applications.create_message_response(content="Test")

        with pytest.raises(modules.applications.InteractionNotFoundError):
            modules.applications.create_interaction_response(
                "invalid_token",
                response,
            )


@pytest.mark.applications
@pytest.mark.integration
class TestAutocompleteResponses:
    """Tests for autocomplete responses."""

    def test_autocomplete_response(self, modules, test_application, user_pool):
        """Test autocomplete response."""
        app, owner = test_application
        user = user_pool.get_user()

        interaction = modules.applications.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE,
            user_id=user.id,
            data={"name": "search", "type": 1},
        )

        response = modules.applications.create_autocomplete_response(
            choices=[
                {"name": "Option 1", "value": "opt1"},
                {"name": "Option 2", "value": "opt2"},
            ]
        )

        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True


@pytest.mark.applications
@pytest.mark.integration
class TestModalResponses:
    """Tests for modal responses."""

    def test_modal_response(self, modules, test_interaction):
        """Test modal response."""
        interaction, app, user = test_interaction

        response = modules.applications.create_modal_response(
            custom_id="test_modal",
            title="Test Modal",
            components=[
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": "input",
                            "label": "Input",
                            "style": 1,
                        }
                    ],
                }
            ],
        )

        result = modules.applications.create_interaction_response(
            interaction.token,
            response,
        )

        assert result is True
        assert response.response_type == InteractionResponseType.MODAL
