"""
Tests for interaction response builders.
"""

import pytest

from src.core.applications import (
    InteractionResponseType,
    create_message_response,
    create_deferred_response,
    create_modal_response,
    create_autocomplete_response,
    create_update_response,
)


@pytest.mark.applications
@pytest.mark.integration
class TestMessageResponse:
    """Tests for message response builder."""

    def test_create_basic_message(self):
        """Test creating basic message response."""
        response = create_message_response(content="Hello!")

        assert response.response_type == InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE
        assert response.content == "Hello!"
        assert response.flags == 0

    def test_create_ephemeral_message(self):
        """Test creating ephemeral message response."""
        response = create_message_response(
            content="Secret message",
            ephemeral=True,
        )

        assert response.flags == 64

    def test_create_message_with_embeds(self):
        """Test creating message with embeds."""
        response = create_message_response(
            content="Check this out:",
            embeds=[
                {"title": "Embed Title", "description": "Embed content"},
            ],
        )

        assert response.embeds is not None
        assert len(response.embeds) == 1
        assert response.embeds[0]["title"] == "Embed Title"

    def test_create_message_with_components(self):
        """Test creating message with components."""
        response = create_message_response(
            content="Click a button:",
            components=[
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "style": 1, "label": "Click", "custom_id": "btn"},
                    ],
                },
            ],
        )

        assert response.components is not None
        assert len(response.components) == 1

    def test_create_message_with_tts(self):
        """Test creating message with TTS."""
        response = create_message_response(
            content="This will be spoken",
            tts=True,
        )

        assert response.tts is True

    def test_create_message_with_allowed_mentions(self):
        """Test creating message with allowed mentions."""
        response = create_message_response(
            content="@everyone",
            allowed_mentions={"parse": []},
        )

        assert response.allowed_mentions == {"parse": []}


@pytest.mark.applications
@pytest.mark.integration
class TestDeferredResponse:
    """Tests for deferred response builder."""

    def test_create_deferred_response(self):
        """Test creating deferred response."""
        response = create_deferred_response()

        assert response.response_type == InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        assert response.flags == 0

    def test_create_deferred_ephemeral(self):
        """Test creating deferred ephemeral response."""
        response = create_deferred_response(ephemeral=True)

        assert response.flags == 64

    def test_create_deferred_update(self):
        """Test creating deferred update response."""
        response = create_deferred_response(update=True)

        assert response.response_type == InteractionResponseType.DEFERRED_UPDATE_MESSAGE


@pytest.mark.applications
@pytest.mark.integration
class TestUpdateResponse:
    """Tests for update response builder."""

    def test_create_update_response(self):
        """Test creating update response."""
        response = create_update_response(content="Updated content")

        assert response.response_type == InteractionResponseType.UPDATE_MESSAGE
        assert response.content == "Updated content"

    def test_create_update_with_components(self):
        """Test creating update with new components."""
        response = create_update_response(
            content="Updated",
            components=[
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "style": 2, "label": "Disabled", "custom_id": "btn", "disabled": True},
                    ],
                },
            ],
        )

        assert response.components is not None
        assert len(response.components) == 1


@pytest.mark.applications
@pytest.mark.integration
class TestModalResponse:
    """Tests for modal response builder."""

    def test_create_modal_response(self):
        """Test creating modal response."""
        response = create_modal_response(
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
                        },
                    ],
                },
            ],
        )

        assert response.response_type == InteractionResponseType.MODAL
        assert response.custom_id == "test_modal"
        assert response.title == "Test Modal"
        assert response.components is not None
        assert len(response.components) == 1


@pytest.mark.applications
@pytest.mark.integration
class TestAutocompleteResponse:
    """Tests for autocomplete response builder."""

    def test_create_autocomplete_response(self):
        """Test creating autocomplete response."""
        response = create_autocomplete_response(
            choices=[
                {"name": "Option 1", "value": "opt1"},
                {"name": "Option 2", "value": "opt2"},
            ],
        )

        assert response.response_type == InteractionResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT
        assert response.choices is not None
        assert len(response.choices) == 2

    def test_create_empty_autocomplete(self):
        """Test creating autocomplete with no choices."""
        response = create_autocomplete_response(choices=[])

        assert response.choices == []
