"""
AdminUsersRouter composer - combines all mixins into the final class.

The AdminUsersRouter class registers all admin user route handlers on a FastAPI
APIRouter instance via register_routes().
"""

from fastapi import APIRouter

from src.api.schemas.admin import (
    UserSearchListResponse,
    UserDetailsResponse,
    UserBadgeUpdateResponse,
    UserNotesResponse,
    ScheduledDeletionListResponse,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse

from .base import AdminUsersRouterBase
from .search import SearchMixin
from .management import ManagementMixin
from .lifecycle import LifecycleMixin
from .admin_users import AdminUsersMixin, AdminUserResponse, AdminUserListResponse


class AdminUsersRouter(
    SearchMixin,
    ManagementMixin,
    LifecycleMixin,
    AdminUsersMixin,
    AdminUsersRouterBase,
):
    def register_routes(self, router: APIRouter) -> None:
        # Search
        router.get(
            "/users/search",
            response_model=UserSearchListResponse,
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid query parameters",
                },
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
            },
        )(self.admin_user_search)

        router.get(
            "/users/scheduled-deletions",
            response_model=ScheduledDeletionListResponse,
            responses={
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
            },
        )(self.admin_list_scheduled_deletions)

        # User details
        router.get(
            "/users/{user_id}",
            response_model=UserDetailsResponse,
            responses={
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.get_user_details)

        # Tier
        router.put(
            "/users/{user_id}/tier",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.update_user_tier)

        # Badges
        router.post(
            "/users/{user_id}/badges/{badge}",
            response_model=UserBadgeUpdateResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid user ID"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.add_user_badge)

        router.delete(
            "/users/{user_id}/badges/{badge}",
            response_model=UserBadgeUpdateResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid user ID"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.remove_user_badge)

        # Notes
        router.get(
            "/users/{user_id}/notes",
            response_model=UserNotesResponse,
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid query parameters",
                },
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.get_user_notes)

        router.post(
            "/users/{user_id}/notes",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.update_user_notes)

        # Lifecycle
        router.post(
            "/users/{user_id}/force-username-change",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.admin_force_username_change)

        router.post(
            "/users/{user_id}/cancel-deletion",
            response_model=SuccessResponse,
            responses={
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.admin_cancel_account_deletion)

        router.post(
            "/users/{user_id}/delay-deletion",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.admin_delay_account_deletion)

        router.post(
            "/users/{user_id}/force-purge",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
            },
        )(self.admin_force_purge_account)

        # Admin user management
        router.get(
            "/admin-users",
            response_model=AdminUserListResponse,
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
            },
        )(self.list_admin_users)

        router.get(
            "/admin-users/{user_id}",
            response_model=AdminUserResponse,
            responses={
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "Admin user not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.get_admin_user)

        router.post(
            "/admin-users",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.create_admin_user)

        router.put(
            "/admin-users/{user_id}",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "Admin user not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.update_admin_user)

        router.delete(
            "/admin-users/{user_id}",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "Admin user not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.delete_admin_user)

        router.post(
            "/admin-users/{user_id}/toggle-status",
            response_model=SuccessResponse,
            responses={
                400: {"model": ErrorResponse, "description": "Invalid request data"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {
                    "model": ErrorResponse,
                    "description": "Insufficient permissions",
                },
                404: {"model": ErrorResponse, "description": "Admin user not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )(self.toggle_admin_user_status)
