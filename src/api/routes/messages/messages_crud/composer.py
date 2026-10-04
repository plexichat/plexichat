"""
MessagesCRUDRouter composer - combines all mixins into the final class.

The MessagesCRUDRouter class registers all message CRUD route handlers
on a FastAPI APIRouter instance via register_routes().
"""

from typing import Any

from fastapi import APIRouter

from src.api.schemas.messages import MessageResponse
from src.api.schemas.common import ErrorResponse, SuccessResponse

from .base import MessagesCRUDBase
from .send import SendMixin
from .retrieve import RetrieveMixin
from .edit import EditMixin
from .delete import DeleteMixin
from .broadcast import BroadcastMixin


_send_responses: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorResponse,
        "description": "Invalid channel ID or empty message",
    },
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Access denied"},
    404: {"model": ErrorResponse, "description": "Channel not found"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}

_get_responses: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorResponse,
        "description": "Invalid channel ID or message ID",
    },
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    404: {"model": ErrorResponse, "description": "Message not found"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}

_edit_responses: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorResponse,
        "description": "Invalid message or channel ID or content",
    },
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Access denied"},
    404: {"model": ErrorResponse, "description": "Message not found"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}

_delete_responses: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid message ID"},
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Access denied"},
    404: {"model": ErrorResponse, "description": "Message not found"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}


class MessagesCRUDRouter(
    SendMixin,
    RetrieveMixin,
    EditMixin,
    DeleteMixin,
    BroadcastMixin,
    MessagesCRUDBase,
):
    def register_routes(self, router: APIRouter) -> None:
        router.post(
            "/channels/{channel_id}/messages",
            response_model=MessageResponse,
            summary="Send message",
            responses=_send_responses,
        )(self.send_channel_message)

        router.get(
            "/channels/{channel_id}/messages/{message_id}",
            response_model=MessageResponse,
            summary="Get message",
            responses=_get_responses,
        )(self.get_message)

        router.patch(
            "/channels/{channel_id}/messages/{message_id}",
            response_model=MessageResponse,
            summary="Edit message",
            responses=_edit_responses,
        )(self.edit_message)

        router.delete(
            "/channels/{channel_id}/messages/{message_id}",
            response_model=SuccessResponse,
            summary="Delete message",
            responses=_delete_responses,
        )(self.delete_message)

        router.post(
            "/channels/{channel_id}/messages/bulk-delete",
            response_model=SuccessResponse,
            summary="Bulk-delete messages",
        )(self.bulk_delete_messages)
