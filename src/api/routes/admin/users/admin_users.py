"""
Admin user management mixin - CRUD and status toggle for admin accounts.
"""

from fastapi import Request, HTTPException
from pydantic import BaseModel

from src.api.schemas.common import SuccessResponse
from ..utils import check_host_restriction, require_admin_permission
from .base import AdminUsersRouterProtocol
import utils.logger as logger


class AdminUserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: str = "admin"


class AdminUserUpdate(BaseModel):
    username: str | None = None
    email: str | None = None
    password: str | None = None
    role: str | None = None


class AdminUserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: int
    last_login_at: int | None = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]


class AdminUsersMixin(AdminUsersRouterProtocol):
    async def list_admin_users(self, request: Request):
        """
        List all admin users.
        """
        check_host_restriction(request)
        require_admin_permission(request, "admin.users")

        try:
            db = self._get_db()
            rows = db.fetch_all(
                "SELECT id, username, email_index, created_at, last_login_at, account_locked FROM auth_users WHERE (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%') ORDER BY created_at DESC"
            )

            users = []
            for row in rows:
                users.append(
                    AdminUserResponse(
                        id=str(row["id"]),
                        username=row["username"],
                        email="[Encrypted]",
                        role="admin",
                        is_active=not row.get("account_locked", 0),
                        created_at=row["created_at"],
                        last_login_at=row["last_login_at"],
                    )
                )

            return AdminUserListResponse(users=users)
        except Exception as e:
            logger.error(f"Failed to list admin users: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def get_admin_user(self, request: Request, user_id: str):
        """
        Get a specific admin user by ID.
        """
        check_host_restriction(request)
        require_admin_permission(request, "admin.users")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)
            row = db.fetch_one(
                "SELECT id, username, email_index, created_at, last_login_at, account_locked FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
                (uid,),
            )

            if not row:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            return AdminUserResponse(
                id=str(row["id"]),
                username=row["username"],
                email="[Encrypted]",
                role="admin",
                is_active=not row.get("account_locked", 0),
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def create_admin_user(self, request: Request, user_data: AdminUserCreate):
        """
        Create a new admin user.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.users")

        try:
            db = self._get_db()
            auth = self._get_auth()

            existing = db.fetch_one(
                "SELECT id FROM auth_users WHERE username = ?", (user_data.username,)
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {"code": 400, "message": "Username already exists"}
                    },
                )

            user = auth.register(
                username=user_data.username,
                email=user_data.email,
                password=user_data.password,
            )

            auth.grant_permission(user.id, "admin.*")
            auth.grant_permission(user.id, "*")

            logger.info(f"Admin user created: {user_data.username} by admin {admin_id}")
            return SuccessResponse(
                success=True, message="Admin user created successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def update_admin_user(
        self, request: Request, user_id: str, user_data: AdminUserUpdate
    ):
        """
        Update an existing admin user.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.users")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)

            user = db.fetch_one(
                "SELECT id, username, email_index FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
                (uid,),
            )
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import can_manage_admin

            if not can_manage_admin(db, admin_id, uid):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot update admins at or above your own position",
                        }
                    },
                )

            updates = []
            params = []

            if user_data.username:
                existing = db.fetch_one(
                    "SELECT id FROM auth_users WHERE username = ? AND id != ?",
                    (user_data.username, uid),
                )
                if existing:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Username already exists"}
                        },
                    )
                updates.append("username = ?")
                params.append(user_data.username)

            if user_data.email:
                auth = self._get_auth()
                if auth:
                    auth.update_user(uid, email=user_data.email)

            if user_data.password:
                from src.utils.encryption import hash_password

                password_hash = hash_password(user_data.password)
                updates.append("password_hash = ?")
                params.append(password_hash)

            if updates:
                params.append(uid)
                column_updates = ", ".join(updates)
                query = f"UPDATE auth_users SET {column_updates} WHERE id = ?"  # nosec - column_updates built from hardcoded strings
                db.execute(query, tuple(params))
                logger.info(f"Admin user updated: {user_id} by admin {admin_id}")

            return SuccessResponse(
                success=True, message="Admin user updated successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def delete_admin_user(self, request: Request, user_id: str):
        """
        Delete an admin user.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.users")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)

            if str(uid) == str(admin_id):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot delete your own account",
                        }
                    },
                )

            user = db.fetch_one(
                "SELECT id FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
                (uid,),
            )
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import can_manage_admin

            if not can_manage_admin(db, admin_id, uid):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot delete admins at or above your own position",
                        }
                    },
                )

            db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))
            logger.info(f"Admin user deleted: {user_id} by admin {admin_id}")

            return SuccessResponse(
                success=True, message="Admin user deleted successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def toggle_admin_user_status(self, request: Request, user_id: str):
        """
        Toggle the active status of an admin user.
        """
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.users")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)

            if str(uid) == str(admin_id):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot disable your own account",
                        }
                    },
                )

            user = db.fetch_one(
                "SELECT id, account_locked FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
                (uid,),
            )
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import can_manage_admin

            if not can_manage_admin(db, admin_id, uid):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot toggle status of admins at or above your own position",
                        }
                    },
                )

            new_status = 0 if user["account_locked"] == 1 else 1
            db.execute(
                "UPDATE auth_users SET account_locked = ? WHERE id = ?",
                (new_status, uid),
            )
            logger.info(
                f"Admin user status toggled: {user_id} to {new_status} by admin {admin_id}"
            )

            return SuccessResponse(
                success=True, message="Admin user status updated successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to toggle admin user status: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
