"""
Lifecycle mixin - User lifecycle endpoints (force username change, cancel/delay
deletion, force purge).
"""

import time
from fastapi import Request, HTTPException

from src.api.schemas.admin import ForceUsernameChangeRequest
from src.api.schemas.common import SuccessResponse
from ..utils import check_host_restriction, require_admin_permission
from .base import AdminUsersRouterProtocol
import utils.logger as logger


class LifecycleMixin(AdminUsersRouterProtocol):
    async def admin_force_username_change(
        self, request: Request, user_id: str, body: ForceUsernameChangeRequest
    ):
        """
        Flag a user account to require a username change upon their next login.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.force_username_change")

        from src.core import admin

        try:
            uid = self._parse_user_id(user_id)
            if body.ban_current:
                user = admin.get_user_details(uid)
                if user:
                    admin.add_banned_username(
                        user.username, body.reason or "Forced change", admin_id, False
                    )
            admin.force_username_change(uid, True)
            return SuccessResponse(
                success=True, message="Username change forced successfully"
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    async def admin_cancel_account_deletion(self, request: Request, user_id: str):
        """
        Cancel a scheduled account deletion and restore the account to 'active' status.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.edit")

        auth = self._get_auth()
        try:
            uid = self._parse_user_id(user_id)
            auth.cancel_account_deletion(uid, admin_id=int(admin_id))
            return SuccessResponse(
                success=True, message="Account deletion cancelled successfully"
            )
        except Exception as e:
            logger.error(f"Admin failed to cancel deletion for {user_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )

    async def admin_delay_account_deletion(
        self, request: Request, user_id: str, deletion_at: int
    ):
        """
        Set a new deletion date for a scheduled account deletion.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.edit")

        auth = self._get_auth()
        try:
            uid = self._parse_user_id(user_id)
            now = int(time.time())
            if deletion_at < now:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "deletion_at must be in the future",
                        }
                    },
                )
            auth.delay_account_deletion(uid, deletion_at, admin_id=int(admin_id))
            logger.info(f"Admin {admin_id} delayed deletion for user {uid}")
            return SuccessResponse(
                success=True, message="Account deletion delayed successfully"
            )
        except (ValueError, Exception) as e:
            error_msg = str(e)
            if "not scheduled for deletion" in error_msg.lower():
                logger.warning(f"Admin failed to delay deletion for {user_id}: {e}")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Account is not scheduled for deletion",
                        }
                    },
                )

            if isinstance(e, ValueError):
                logger.warning(f"Admin failed to delay deletion for {user_id}: {e}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": str(e)}},
                )

            logger.error(f"Admin failed to delay deletion for {user_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )

    async def admin_force_purge_account(self, request: Request, user_id: str):
        """
        Immediately purge a user account, bypassing the grace period.
        This is irreversible and should only be used in extreme cases.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "users.force_purge")

        auth = self._get_auth()
        try:
            uid = self._parse_user_id(user_id)
            auth.force_purge_account(uid, admin_id=int(admin_id))
            logger.warning(f"Admin {admin_id} force-purged user {uid}")
            return SuccessResponse(success=True, message="Account purged successfully")
        except Exception as e:
            logger.error(f"Admin failed to force purge {user_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )
