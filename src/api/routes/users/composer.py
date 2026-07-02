"""
UsersRouter composer - combines all mixins into the final class.

The UsersRouter class registers all user route handlers on a FastAPI APIRouter
instance via register_routes().
"""

from typing import List

from fastapi import APIRouter

from src.api.schemas.auth import UserResponse
from src.api.schemas.users import UserPublicResponse, UserAvatarResponse
from src.api.schemas.channels import DMChannelResponse, NotesChannelResponse
from src.api.schemas.messages import MessagingSettingsResponse
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.api.schemas.dsar import (
    DSARRequestResponse,
    DSARRequestListResponse,
)

from .base import UsersRouterBase
from .profile import ProfileMixin
from .avatar import AvatarMixin
from .channel import ChannelMixin
from .discovery import DiscoveryMixin
from .settings import SettingsMixin
from .dsar import DataExportMixin


class UsersRouter(
    ProfileMixin,
    AvatarMixin,
    ChannelMixin,
    DiscoveryMixin,
    SettingsMixin,
    DataExportMixin,
    UsersRouterBase,
):
    def register_routes(self, router: APIRouter) -> None:
        router.get(
            "/@me",
            response_model=UserResponse,
            summary="Get current user",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {"model": ErrorResponse, "description": "User not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_current_user_info)

        router.patch(
            "/@me",
            response_model=UserResponse,
            summary="Update current user",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid update data"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {
                    "model": ErrorResponse,
                    "description": "Incorrect current password",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
                409: {"model": ErrorResponse, "description": "User already exists"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.update_current_user)

        router.post(
            "/@me/avatar",
            response_model=UserAvatarResponse,
            summary="Upload avatar",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid file or upload error",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.upload_avatar)

        router.get(
            "/@me/notes",
            response_model=NotesChannelResponse,
            summary="Get user notes channel",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
                501: {"model": ErrorResponse, "description": "Notes not implemented"},
            },
        )(self.get_notes_channel)

        router.get(
            "/@me/channels",
            response_model=List[DMChannelResponse],
            summary="List DM channels",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_dm_channels)

        router.post(
            "/@me/channels",
            response_model=DMChannelResponse,
            summary="Create DM channel",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid recipient ID"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {
                    "model": ErrorResponse,
                    "description": "Cannot message this user",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
                501: {
                    "model": ErrorResponse,
                    "description": "DM creation not implemented",
                },
            },
        )(self.create_dm_channel)

        router.get(
            "/search",
            response_model=UserPublicResponse,
            summary="Search user",
            responses={
                400: {"model": ErrorResponse, "description": "Username required"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {"model": ErrorResponse, "description": "User not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.search_user_by_username)

        router.get(
            "/{user_id}",
            response_model=UserPublicResponse,
            summary="Get user by ID",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid user ID"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {"model": ErrorResponse, "description": "User not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_user)

        router.get(
            "/@me/messaging-settings",
            response_model=MessagingSettingsResponse,
            summary="Get messaging settings",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_messaging_settings)

        router.patch(
            "/@me/messaging-settings",
            response_model=MessagingSettingsResponse,
            summary="Update messaging settings",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.update_messaging_settings)

        router.delete(
            "/@me",
            response_model=SuccessResponse,
            summary="Delete account",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid password or 2FA code",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.schedule_account_deletion)

        # Data export (DSAR - GDPR Article 20 Right to Portability)
        router.post(
            "/@me/data-export",
            response_model=SuccessResponse,
            summary="Request a data export",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Incorrect password"},
                429: {
                    "model": ErrorResponse,
                    "description": "Rate limit exceeded (1 request per 24h)",
                },
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.request_data_export)

        router.get(
            "/@me/data-export",
            response_model=DSARRequestListResponse,
            summary="List your data export requests",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_data_export_requests)

        router.get(
            "/@me/data-export/{request_id}",
            response_model=DSARRequestResponse,
            summary="Get a specific data export request",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request ID"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {"model": ErrorResponse, "description": "Request not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_data_export_status)

        router.delete(
            "/@me/data-export/{request_id}",
            response_model=SuccessResponse,
            summary="Cancel a data export request",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Cannot cancel this request",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {"model": ErrorResponse, "description": "Request not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.cancel_data_export_request)

        # The download endpoint streams the file directly via StreamingResponse,
        # so no response_model is declared (return type is implicit).
        router.get(
            "/@me/data-export/{request_id}/download",
            summary="Download a ready data export",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Export is not ready for download",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {
                    "model": ErrorResponse,
                    "description": "Request or export file not found",
                },
                410: {"model": ErrorResponse, "description": "Download link expired"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.download_data_export)
