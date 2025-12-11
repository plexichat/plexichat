"""
Interaction responses - Build interaction response payloads.
"""

from typing import List, Dict, Any, Optional

from ..models import InteractionResponse, InteractionResponseType


EPHEMERAL_FLAG = 64


def create_message_response(
    content: Optional[str] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    components: Optional[List[Dict[str, Any]]] = None,
    ephemeral: bool = False,
    tts: bool = False,
    allowed_mentions: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> InteractionResponse:
    """
    Create a channel message response.
    
    Args:
        content: Message content
        embeds: Message embeds
        components: Message components
        ephemeral: Whether message is ephemeral (only visible to user)
        tts: Whether to use text-to-speech
        allowed_mentions: Allowed mentions configuration
        attachments: Message attachments
        
    Returns:
        InteractionResponse
    """
    flags = EPHEMERAL_FLAG if ephemeral else 0

    return InteractionResponse(
        response_type=InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
        content=content,
        embeds=embeds,
        components=components,
        flags=flags,
        tts=tts,
        allowed_mentions=allowed_mentions,
        attachments=attachments,
    )


def create_deferred_response(
    ephemeral: bool = False,
    update: bool = False,
) -> InteractionResponse:
    """
    Create a deferred response.
    
    Args:
        ephemeral: Whether eventual message is ephemeral
        update: Whether this is a deferred update (for components)
        
    Returns:
        InteractionResponse
    """
    flags = EPHEMERAL_FLAG if ephemeral else 0

    response_type = (
        InteractionResponseType.DEFERRED_UPDATE_MESSAGE if update
        else InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    )

    return InteractionResponse(
        response_type=response_type,
        flags=flags,
    )


def create_update_response(
    content: Optional[str] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    components: Optional[List[Dict[str, Any]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> InteractionResponse:
    """
    Create an update message response (for component interactions).
    
    Args:
        content: New message content
        embeds: New message embeds
        components: New message components
        attachments: New message attachments
        
    Returns:
        InteractionResponse
    """
    return InteractionResponse(
        response_type=InteractionResponseType.UPDATE_MESSAGE,
        content=content,
        embeds=embeds,
        components=components,
        attachments=attachments,
    )


def create_modal_response(
    custom_id: str,
    title: str,
    components: List[Dict[str, Any]],
) -> InteractionResponse:
    """
    Create a modal popup response.
    
    Args:
        custom_id: Modal custom ID
        title: Modal title
        components: Modal components (action rows with text inputs)
        
    Returns:
        InteractionResponse
    """
    return InteractionResponse(
        response_type=InteractionResponseType.MODAL,
        custom_id=custom_id,
        title=title,
        components=components,
    )


def create_autocomplete_response(
    choices: List[Dict[str, Any]],
) -> InteractionResponse:
    """
    Create an autocomplete results response.
    
    Args:
        choices: List of autocomplete choices
        
    Returns:
        InteractionResponse
    """
    return InteractionResponse(
        response_type=InteractionResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT,
        choices=choices,
    )


def response_to_dict(response: InteractionResponse) -> Dict[str, Any]:
    """
    Convert an interaction response to dict format.
    
    Args:
        response: InteractionResponse
        
    Returns:
        Dict for JSON serialization
    """
    result: Dict[str, Any] = {
        "type": response.response_type.value if isinstance(response.response_type, InteractionResponseType) else response.response_type,
    }

    if response.response_type == InteractionResponseType.MODAL:
        result["data"] = {
            "custom_id": response.custom_id,
            "title": response.title,
            "components": response.components,
        }
    elif response.response_type == InteractionResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT:
        result["data"] = {
            "choices": response.choices or [],
        }
    elif response.response_type in (
        InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
        InteractionResponseType.UPDATE_MESSAGE,
    ):
        data = {}
        if response.content is not None:
            data["content"] = response.content
        if response.embeds:
            data["embeds"] = response.embeds
        if response.components:
            data["components"] = response.components
        if response.flags:
            data["flags"] = response.flags
        if response.tts:
            data["tts"] = response.tts
        if response.allowed_mentions:
            data["allowed_mentions"] = response.allowed_mentions
        if response.attachments:
            data["attachments"] = response.attachments
        result["data"] = data
    elif response.response_type in (
        InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
        InteractionResponseType.DEFERRED_UPDATE_MESSAGE,
    ):
        if response.flags:
            result["data"] = {"flags": response.flags}

    return result
