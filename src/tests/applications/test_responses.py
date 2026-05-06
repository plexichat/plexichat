"""Tests for application interaction responses."""

import pytest

from src.core.applications.models import (
    InteractionResponseType,
    InteractionResponse,
    ButtonStyle,
    ComponentType,
)


@pytest.mark.applications
class TestResponses:
    """Tests for interaction response models and types."""

    def test_pong_response(self):
        """Test creating a pong response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.PONG,
        )
        assert response.response_type == InteractionResponseType.PONG
        assert response.content is None

    def test_message_response(self):
        """Test creating a channel message response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Hello!",
        )
        assert (
            response.response_type
            == InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE
        )
        assert response.content == "Hello!"

    def test_deferred_message_response(self):
        """Test creating a deferred message response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
        )
        assert (
            response.response_type
            == InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        )

    def test_update_message_response(self):
        """Test creating an update message response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.UPDATE_MESSAGE,
            content="Updated!",
        )
        assert response.response_type == InteractionResponseType.UPDATE_MESSAGE

    def test_autocomplete_response(self):
        """Test creating an autocomplete result response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT,
            choices=[
                {"name": "Option A", "value": "a"},
                {"name": "Option B", "value": "b"},
            ],
        )
        assert len(response.choices) == 2

    def test_modal_response(self):
        """Test creating a modal response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.MODAL,
            custom_id="modal_test",
            title="Test Modal",
            components=[
                {"type": 1, "components": []},
            ],
        )
        assert response.custom_id == "modal_test"
        assert response.title == "Test Modal"

    def test_ephemeral_response(self):
        """Test creating an ephemeral response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Secret message",
            flags=64,  # EPHEMERAL flag
        )
        assert response.flags == 64

    def test_response_with_embeds(self):
        """Test creating a response with embeds."""
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Embedded content",
            embeds=[{"title": "Test", "description": "An embed"}],
        )
        assert len(response.embeds) == 1

    def test_response_with_components(self):
        """Test creating a response with components."""
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Choose an option",
            components=[
                {
                    "type": ComponentType.ACTION_ROW.value,
                    "components": [
                        {
                            "type": ComponentType.BUTTON.value,
                            "style": ButtonStyle.PRIMARY.value,
                            "label": "Click",
                            "custom_id": "btn1",
                        }
                    ],
                }
            ],
        )
        assert len(response.components) == 1

    def test_response_with_tts(self):
        """Test creating a TTS response."""
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Speaking!",
            tts=True,
        )
        assert response.tts is True

    def test_response_with_allowed_mentions(self):
        """Test creating a response with allowed mentions."""
        response = InteractionResponse(
            response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            content="Hello <@12345>!",
            allowed_mentions={"parse": ["users"]},
        )
        assert response.allowed_mentions is not None
