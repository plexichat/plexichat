"""
Admin user management mixin - CRUD and status toggle for admin accounts.
"""

import time

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
    role: str = "support_admin"


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


def _row_value(row, key: str, index: int, default=None):
    """Read a database row consistently across dict and tuple drivers."""
    if isinstance(row, dict):
        value = row.get(key, default)
    else:
        value = row[index] if len(row) > index else default
    return default if value is None else value


def _required_row_value(row, key: str, index: int, value_type):
    """Read and type-check a required database row value."""
    value = _row_value(row, key, index)
    if not isinstance(value, value_type):
        raise RuntimeError(f"Missing or invalid database value: {key}")
    return value


def _admin_user_query(where: str = "") -> str:
    """Return the canonical admin-user projection used by read endpoints."""
    return f"""
        SELECT a.id, a.username, a.email, a.created_at,
               a.last_login AS last_login_at,
               COALESCE(u.account_locked, 0) AS account_locked,
               COALESCE((SELECT r.name
                         FROM admin_role_assignments ra
                         JOIN admin_roles r ON r.id = ra.role_id
                         WHERE ra.admin_id = a.id
                         ORDER BY r.position DESC LIMIT 1), 'unassigned') AS role
        FROM admin_users a
        LEFT JOIN auth_users u ON u.id = a.id
        {where}
    """


class AdminUsersMixin(AdminUsersRouterProtocol):
    async def list_admin_users(self, request: Request):
        """List administrator accounts from the canonical admin tables."""
        check_host_restriction(request)
        require_admin_permission(request, "admin.read")

        try:
            db = self._get_db()
            rows = db.fetch_all(_admin_user_query("ORDER BY a.created_at DESC"))
            users = [
                AdminUserResponse(
                    id=str(_row_value(row, "id", 0)),
                    username=_required_row_value(row, "username", 1, str),
                    email="[Encrypted]",
                    role=_row_value(row, "role", 6, "unassigned"),
                    is_active=not bool(_row_value(row, "account_locked", 5, 0)),
                    created_at=_required_row_value(row, "created_at", 3, int),
                    last_login_at=_row_value(row, "last_login_at", 4),
                )
                for row in rows
            ]
            return AdminUserListResponse(users=users)
        except Exception as e:
            logger.error(f"Failed to list admin users: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    async def get_admin_user(self, request: Request, user_id: str):
        """Get one administrator account by ID."""
        check_host_restriction(request)
        require_admin_permission(request, "admin.read")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)
            row = db.fetch_one(_admin_user_query("WHERE a.id = ?"), (uid,))
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )
            return AdminUserResponse(
                id=str(_row_value(row, "id", 0)),
                username=_required_row_value(row, "username", 1, str),
                email="[Encrypted]",
                role=_row_value(row, "role", 6, "unassigned"),
                is_active=not bool(_row_value(row, "account_locked", 5, 0)),
                created_at=_required_row_value(row, "created_at", 3, int),
                last_login_at=_row_value(row, "last_login_at", 4),
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    async def create_admin_user(self, request: Request, user_data: AdminUserCreate):
        """Create a new administrator with a hierarchy-checked role."""
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.edit")

        try:
            db = self._get_db()
            auth = self._get_auth()
            from src.core.admin.permissions import can_manage_role, log_admin_action

            requested_role = user_data.role
            role = db.fetch_one(
                "SELECT id FROM admin_roles WHERE name = ?", (requested_role,)
            )
            if not role and requested_role == "admin":
                import json
                from src.utils.encryption import generate_snowflake_id

                role_id = generate_snowflake_id()
                now_ms = int(time.time() * 1000)
                db.execute(
                    """INSERT INTO admin_roles
                       (id, name, description, permissions, created_at, created_by, is_system, position)
                       VALUES (?, ?, ?, ?, ?, ?, 0, 10)""",
                    (
                        role_id,
                        "admin",
                        "Standard administrative access",
                        json.dumps({"admin.read": True, "admin.edit": True}),
                        now_ms,
                        admin_id,
                    ),
                )
                role = {"id": role_id}
            if not role:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Requested admin role not found",
                        }
                    },
                )
            role_id = _required_row_value(role, "id", 0, int)
            if not can_manage_role(db, admin_id, role_id):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot assign this admin role",
                        }
                    },
                )

            existing = db.fetch_one(
                "SELECT id FROM auth_users WHERE LOWER(username) = LOWER(?)",
                (user_data.username,),
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
            password_row = db.fetch_one(
                "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
            )
            password_hash = _row_value(password_row, "password_hash", 0)
            if not password_hash:
                raise RuntimeError("Created admin has no password hash")

            now_ms = int(time.time() * 1000)
            db.execute(
                """INSERT INTO admin_users
                   (id, username, password_hash, email, created_at, must_setup_otp)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (user.id, user_data.username, password_hash, user_data.email, now_ms),
            )
            db.execute(
                """INSERT INTO admin_role_assignments
                   (admin_id, role_id, assigned_at, assigned_by)
                   VALUES (?, ?, ?, ?)""",
                (user.id, role_id, now_ms, admin_id),
            )
            log_admin_action(
                db,
                admin_id,
                "admin_user.create",
                "admin_user",
                user.id,
                {"username": user_data.username, "role": requested_role},
                request.client.host if request.client else "unknown",
            )
            logger.info(f"Admin user created: {user_data.username} by admin {admin_id}")
            return SuccessResponse(
                success=True, message="Admin user created successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    async def update_admin_user(
        self, request: Request, user_id: str, user_data: AdminUserUpdate
    ):
        """Update an administrator without bypassing admin security policy."""
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.edit")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)
            user = db.fetch_one(
                "SELECT id, username FROM admin_users WHERE id = ?", (uid,)
            )
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import (
                can_manage_admin,
                can_manage_role,
                log_admin_action,
            )

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

            auth = self._get_auth()
            changed = {}
            if user_data.username:
                existing = db.fetch_one(
                    "SELECT id FROM auth_users WHERE LOWER(username) = LOWER(?) AND id != ?",
                    (user_data.username, uid),
                )
                if existing:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Username already exists"}
                        },
                    )
                auth.update_user(uid, username=user_data.username)
                db.execute(
                    "UPDATE admin_users SET username = ? WHERE id = ?",
                    (user_data.username, uid),
                )
                changed["username"] = True

            if user_data.email:
                auth.update_user(uid, email=user_data.email)
                db.execute(
                    "UPDATE admin_users SET email = ? WHERE id = ?",
                    (user_data.email, uid),
                )
                changed["email"] = True

            if user_data.password:
                from src.core.admin import auth as admin_auth

                success, message = admin_auth.set_password(db, uid, user_data.password)
                if not success:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": message}},
                    )
                changed["password"] = True

            if user_data.role:
                requested_role = user_data.role
                role = db.fetch_one(
                    "SELECT id FROM admin_roles WHERE name = ?", (requested_role,)
                )
                if not role:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {
                                "code": 400,
                                "message": "Requested admin role not found",
                            }
                        },
                    )
                role_id = _required_row_value(role, "id", 0, int)
                if not can_manage_role(db, admin_id, role_id):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Cannot assign this admin role",
                            }
                        },
                    )
                db.execute(
                    "DELETE FROM admin_role_assignments WHERE admin_id = ?", (uid,)
                )
                db.execute(
                    """INSERT INTO admin_role_assignments
                       (admin_id, role_id, assigned_at, assigned_by)
                       VALUES (?, ?, ?, ?)""",
                    (uid, role_id, int(time.time() * 1000), admin_id),
                )
                changed["role"] = requested_role

            log_admin_action(
                db,
                admin_id,
                "admin_user.update",
                "admin_user",
                uid,
                changed,
                request.client.host if request.client else "unknown",
            )
            logger.info(f"Admin user updated: {user_id} by admin {admin_id}")
            return SuccessResponse(
                success=True, message="Admin user updated successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    async def delete_admin_user(self, request: Request, user_id: str):
        """Delete an administrator and its role assignments."""
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.edit")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)
            if uid == admin_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot delete your own account",
                        }
                    },
                )
            if not db.fetch_one("SELECT id FROM admin_users WHERE id = ?", (uid,)):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import can_manage_admin, log_admin_action

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
            db.execute("DELETE FROM admin_role_assignments WHERE admin_id = ?", (uid,))
            db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (uid,))
            db.execute("DELETE FROM admin_users WHERE id = ?", (uid,))
            db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))
            log_admin_action(
                db,
                admin_id,
                "admin_user.delete",
                "admin_user",
                uid,
                {},
                request.client.host if request.client else "unknown",
            )
            logger.info(f"Admin user deleted: {user_id} by admin {admin_id}")
            return SuccessResponse(
                success=True, message="Admin user deleted successfully"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete admin user: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    async def toggle_admin_user_status(self, request: Request, user_id: str):
        """Toggle the corresponding auth account's active status."""
        check_host_restriction(request)
        admin_id = require_admin_permission(request, "admin.edit")

        try:
            db = self._get_db()
            uid = self._parse_user_id(user_id)
            if uid == admin_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot disable your own account",
                        }
                    },
                )
            if not db.fetch_one("SELECT id FROM admin_users WHERE id = ?", (uid,)):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import can_manage_admin, log_admin_action

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
            row = db.fetch_one(
                "SELECT account_locked FROM auth_users WHERE id = ?", (uid,)
            )
            current_locked = bool(_row_value(row, "account_locked", 0, 0))
            new_status = 0 if current_locked else 1
            db.execute(
                "UPDATE auth_users SET account_locked = ? WHERE id = ?",
                (new_status, uid),
            )
            if new_status:
                db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (uid,))
            log_admin_action(
                db,
                admin_id,
                "admin_user.toggle_status",
                "admin_user",
                uid,
                {"account_locked": bool(new_status)},
                request.client.host if request.client else "unknown",
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
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
