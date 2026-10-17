"""Tests for application interaction handling."""

import pytest
from fastapi import HTTPException
from unittest.mock import patch

from src.core.applications.exceptions import InteractionNotFoundError
from src.core.applications.models import (
    InteractionType,
    InteractionResponseType,
    InteractionResponse,
)


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
        assert interaction is not None
        assert interaction.application_id == app.id
        mock_events.dispatch.assert_called()
        call_args = mock_events.dispatch.call_args
        event = call_args[0][0]
        assert event.event_type.value == "INTERACTION_CREATE"

    def test_response_rejects_mismatched_application(self, app_manager, test_user):
        """An interaction token cannot be used for another application."""
        app = app_manager.create_application(owner_id=test_user.id, name="Bound App")
        interaction = app_manager.handle_interaction(
            application_id=app.id,
            interaction_type=InteractionType.APPLICATION_COMMAND,
            user_id=test_user.id,
        )
        response = InteractionResponse(
            response_type=InteractionResponseType.PONG,
        )

        with pytest.raises(InteractionNotFoundError):
            app_manager.create_interaction_response(
                interaction.token,
                response,
                application_id=app.id + 1,
            )

        # The mismatch must not consume the one-shot interaction.
        assert (
            app_manager.create_interaction_response(
                interaction.token,
                response,
                application_id=app.id,
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_callback_binds_application_and_passes_payload(self):
        """The HTTP callback forwards the verified application binding."""
        from src.api.routes import applications as application_routes

        body = application_routes.InteractionCallbackRequest(
            application_id=42,
            type=InteractionResponseType.PONG,
        )
        with patch.object(
            application_routes, "check_rate_limit", return_value=(True, 29)
        ):
            with patch.object(
                application_routes.applications,
                "create_interaction_response",
                return_value=True,
            ) as create_response:
                result = await application_routes.respond_to_interaction(
                    "int.123.secret", body
                )

        assert result is None
        create_response.assert_called_once()
        assert create_response.call_args.kwargs["application_id"] == 42

    @pytest.mark.asyncio
    async def test_callback_rate_limit_rejects_before_token_processing(self):
        """Exhausted callbacks return 429 without invoking interaction code."""
        from src.api.routes import applications as application_routes

        body = application_routes.InteractionCallbackRequest(
            application_id=42,
            type=InteractionResponseType.PONG,
        )
        with patch.object(
            application_routes, "check_rate_limit", return_value=(False, 0)
        ):
            with patch.object(
                application_routes.applications,
                "create_interaction_response",
            ) as create_response:
                with pytest.raises(HTTPException) as error:
                    await application_routes.respond_to_interaction(
                        "int.123.secret", body
                    )

        assert error.value.status_code == 429
        assert error.value.headers["Retry-After"] == "60"
        create_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_maps_application_mismatch_to_not_found(self):
        """A token cannot be replayed against a different application."""
        from src.api.routes import applications as application_routes

        body = application_routes.InteractionCallbackRequest(
            application_id=99,
            type=InteractionResponseType.PONG,
        )
        with patch.object(
            application_routes, "check_rate_limit", return_value=(True, 29)
        ):
            with patch.object(
                application_routes.applications,
                "create_interaction_response",
                side_effect=InteractionNotFoundError("wrong application"),
            ):
                with pytest.raises(HTTPException) as error:
                    await application_routes.respond_to_interaction(
                        "int.123.secret", body
                    )

        assert error.value.status_code == 404

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
