"""Tests for application interaction handling."""

import pytest

from src.core.applications.models import (
    InteractionType,
    InteractionResponseType,
    InteractionResponse,
)
from src.core.applications.exceptions import InteractionExpiredError


@pytest.mark.applications
class TestInteractionHandling:
    """Tests for creating and responding to interactions."""

    def test_create_application_command_interaction(self, app_manager, test_user):
        """Test creating an application command interaction."""
        app = app_manager.create_application(owner_id=test_user.id, name="Interact App")
        cmd = app_manager.register_command(
            application_id=app.id, name="test_cmd", description="Test"
        )
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
            data={"id": cmd.id, "name": "test_cmd"},
        )
        assert interaction is not None
        assert interaction.application_id == app.id
        assert interaction.interaction_type == InteractionType.APPLICATION_COMMAND
        assert interaction.user_id == test_user.id

    def test_ping_interaction(self, app_manager, test_user):
        """Test creating a ping interaction."""
        app = app_manager.create_application(owner_id=test_user.id, name="Ping App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.PING,
            user_id=test_user.id,
        )
        assert interaction is not None
        assert interaction.interaction_type == InteractionType.PING

    def test_message_component_interaction(self, app_manager, test_user):
        """Test creating a message component interaction."""
        app = app_manager.create_application(
            owner_id=test_user.id, name="Component App"
        )
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.MESSAGE_COMPONENT,
            user_id=test_user.id,
            data={"custom_id": "btn_click", "component_type": 2},
        )
        assert interaction.interaction_type == InteractionType.MESSAGE_COMPONENT

    def test_interaction_has_token(self, app_manager, test_user):
        """Test that interaction gets a unique token."""
        app = app_manager.create_application(owner_id=test_user.id, name="Token App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
            data={"name": "test"},
        )
        assert interaction.token is not None
        assert len(interaction.token) > 0

    def test_interaction_response_types(self):
        """Test all interaction response types exist."""
        assert InteractionResponseType.PONG.value == 1
        assert InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE.value == 4
        assert InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE.value == 5
        assert InteractionResponseType.UPDATE_MESSAGE.value == 7
        assert InteractionResponseType.MODAL.value == 9

    def test_create_interaction_response(self, app_manager, test_user):
        """Test responding to an interaction."""
        app = app_manager.create_application(owner_id=test_user.id, name="Response App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
            data={"name": "test"},
        )
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Hello from the app!",
        )
        result = app_manager.create_interaction_response(interaction.token, response)
        assert result is True

    def test_interaction_with_server_context(self, app_manager, test_user, test_server):
        """Test interaction with server and channel context."""
        server, owner = test_server
        app = app_manager.create_application(owner_id=test_user.id, name="Context App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
            server_id=server.id,
            channel_id=12345,
            data={"name": "test"},
        )
        assert interaction.server_id == server.id

    def test_interaction_expiry_is_in_milliseconds(self, app_manager, test_user):
        """Test that interaction token expiry uses millisecond units."""
        app = app_manager.create_application(owner_id=test_user.id, name="Expiry App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
            data={"name": "test"},
        )
        handler = app_manager._interactions
        created_at = interaction.created_at
        timeout_config = handler._config.get("interaction_timeout", 900)
        now = handler._current_time()
        remaining = (created_at + timeout_config * 1000) - now
        assert remaining > 800000, f"Interaction expiry too short: {remaining}ms"

    def test_dispatch_interaction_calls_events(self, app_manager, test_user):
        """Test that dispatch_interaction actually dispatches events."""
        from unittest.mock import MagicMock

        mock_events = MagicMock()
        app_manager._interactions._events = mock_events

        app = app_manager.create_application(owner_id=test_user.id, name="Dispatch App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
            data={"name": "test"},
        )
        mock_events.dispatch.assert_called()
        call_args = mock_events.dispatch.call_args
        event = call_args[0][0]
        assert event.event_type.value == "INTERACTION_CREATE"

    def test_component_response_rejects_pong(self, app_manager, test_user):
        """Test that message component interactions cannot respond with PONG."""
        app = app_manager.create_application(
            owner_id=test_user.id, name="CompReject App"
        )
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.MESSAGE_COMPONENT,
            user_id=test_user.id,
            data={"custom_id": "btn", "component_type": 2},
        )
        response = InteractionResponse(
            response_type=InteractionResponseType.PONG,
        )
        with pytest.raises(Exception):
            app_manager.create_interaction_response(interaction.token, response)
