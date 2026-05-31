"""
Search mixin - User search and scheduled deletions endpoints.
"""

import time
from fastapi import Request, HTTPException

from src.api.schemas.admin import (
    UserSearchListResponse,
    UserSearchResponse,
    ScheduledDeletionListResponse,
    ScheduledDeletionResponse,
)
from ..utils import check_host_restriction, require_admin_permission
from .base import AdminUsersRouterProtocol
import utils.logger as logger


class SearchMixin(AdminUsersRouterProtocol):
    async def admin_user_search(
        self, request: Request, q: str, limit: int = 20, offset: int = 0
    ):
        """
        Search for users based on a query string.

        Supports matching by username or email and provides paginated results.
        """
        check_host_restriction(request)
        require_admin_permission(request, "users.read")

        from src.core import admin

        try:
            users_data = admin.search_users(q, limit, offset)
            return UserSearchListResponse(
                users=[
                    UserSearchResponse(
                        id=str(u.id),
                        username=u.username,
                        email=u.email,
                        tier=u.tier,
                        badges=u.badges,
                        created_at=u.created_at,
                        deletion_status=u.deletion_status,
                        deletion_at=u.deletion_at,
                    )
                    for u in users_data
                ]
            )
        except Exception as e:
            logger.error(f"User search error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def admin_list_scheduled_deletions(self, request: Request):
        """
        List all accounts currently in the 30-day grace period for deletion.
        """
        check_host_restriction(request)
        require_admin_permission(request, "users.read")

        db = self._get_db()
        grace_days = 30

        rows = db.fetch_all(
            "SELECT id, username, deletion_at FROM auth_users WHERE deletion_status = 'frozen' ORDER BY deletion_at ASC"
        )

        now = int(time.time())
        deletions = []
        for row in rows:
            deletion_at = row["deletion_at"]
            if deletion_at is None:
                logger.warning(f"User {row['id']} has frozen status but no deletion_at")
                continue
            scheduled_at = deletion_at - (grace_days * 86400)
            days_left = max(0, (deletion_at - now) // 86400)

            logger.info(
                f"Scheduled deletion: user={row['username']}, deletion_at={deletion_at}, scheduled_at={scheduled_at}, days_left={days_left}"
            )

            deletions.append(
                ScheduledDeletionResponse(
                    user_id=str(row["id"]),
                    username=row["username"],
                    scheduled_at=scheduled_at,
                    deletion_at=deletion_at,
                    days_left=days_left,
                )
            )

        return ScheduledDeletionListResponse(deletions=deletions)
