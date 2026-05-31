"""
Management mixin - User profile CRUD, badges, notes, and tier endpoints.
"""

from fastapi import Request, HTTPException

from src.api.schemas.admin import (
    UserDetailsResponse,
    UserTierUpdate,
    UserBadgeUpdateResponse,
    UserNotesResponse,
    UserNotesUpdate,
)
from src.api.schemas.common import SuccessResponse
from ..utils import check_host_restriction, require_admin_permission
from .base import AdminUsersRouterProtocol


class ManagementMixin(AdminUsersRouterProtocol):
    async def get_user_details(self, request: Request, user_id: str):
        """
        Retrieve comprehensive information for a specific user.
        """
        check_host_restriction(request)
        require_admin_permission(request, "users.read")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            user = admin.get_user_details(uid)
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            return UserDetailsResponse(
                id=str(user.id),
                username=user.username,
                email=user.email,
                tier=user.tier,
                badges=user.badges,
                created_at=user.created_at,
                last_login=user.last_login,
                account_locked=user.account_locked,
                locked_until=user.locked_until,
                force_username_change=user.force_username_change,
                deletion_status=user.deletion_status,
                deletion_at=user.deletion_at,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    async def update_user_tier(
        self, request: Request, user_id: str, update: UserTierUpdate
    ):
        """
        Change the account tier (e.g., standard, premium) for a user.
        """
        check_host_restriction(request)
        require_admin_permission(request, "users.tier")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            if not admin.update_user_tier(uid, update.tier):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            return SuccessResponse(
                success=True, message="User tier updated successfully"
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    async def add_user_badge(self, request: Request, user_id: str, badge: str):
        """
        Assign a specific badge to a user's profile.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.badges")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            badges = admin.add_user_badge(uid, badge, admin_id)
            if badges is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            return UserBadgeUpdateResponse(success=True, badges=badges)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    async def remove_user_badge(self, request: Request, user_id: str, badge: str):
        """
        Remove a badge from a user's profile.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.badges")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            badges = admin.remove_user_badge(uid, badge, admin_id)
            if badges is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            return UserBadgeUpdateResponse(success=True, badges=badges)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    async def get_user_notes(self, request: Request, user_id: str):
        """
        Retrieve internal administrator notes for a specific user.
        """
        check_host_restriction(request)
        require_admin_permission(request, "users.notes")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            notes = admin.get_user_notes(uid)
            return UserNotesResponse(user_id=user_id, notes=notes)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    async def update_user_notes(
        self, request: Request, user_id: str, body: UserNotesUpdate
    ):
        """
        Update the internal administrator notes for a user.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.notes")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            admin.save_user_notes(uid, body.notes, admin_id)
            return SuccessResponse(
                success=True, message="User notes saved successfully"
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )
