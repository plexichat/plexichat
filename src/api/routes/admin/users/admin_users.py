"""
Admin user management mixin - CRUD and status toggle for admin accounts.
"""

import time

from fastapi import Request, HTTPException, Query
from pydantic import BaseModel

from src.api.schemas.common import SuccessResponse
from src.api.schemas.policy_types import UIText
from src.core.auth.privilege_cache import invalidate_privilege_caches
from ..utils import (
    check_host_restriction,
    require_admin_permission,
    require_approved_request,
)
from .base import AdminUsersRouterProtocol
import utils.logger as logger


class AdminUserCreate(BaseModel):
    username: UIText
    email: str
    password: str
    role: str = "support_admin"


class AdminUserUpdate(BaseModel):
    username: UIText | None = None
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
    """Return the canonical admin-user projection used by read endpoints.

    The top role used to be a correlated scalar subquery (one lookup per admin
    row). Ranking assignments once in a derived table keeps the result
    identical while making the read a single set-based pass.
    """
    return f"""
        SELECT a.id, a.username, a.email, a.created_at,
               a.last_login AS last_login_at,
               COALESCE(u.account_locked, 0) AS account_locked,
               COALESCE(top_role.name, 'unassigned') AS role
        FROM admin_users a
        LEFT JOIN auth_users u ON u.id = a.id
        LEFT JOIN (
            SELECT admin_id, name
            FROM (
                SELECT ra.admin_id AS admin_id,
                       r.name AS name,
                       ROW_NUMBER() OVER (
                           PARTITION BY ra.admin_id
                           ORDER BY r.position DESC, ra.role_id DESC
                       ) AS role_rank
                FROM admin_role_assignments ra
                JOIN admin_roles r ON r.id = ra.role_id
            ) ranked_roles
            WHERE role_rank = 1
        ) top_role ON top_role.admin_id = a.id
        {where}
    """


def _load_admin_hierarchy(db, current_admin_id: int, target_admin_id: int) -> dict:
    """Resolve target existence/lock state and both positions in one query.

    The mutating admin-user routes previously did a ``SELECT id`` existence
    check, ``can_manage_admin`` (two more position queries) and, for the status
    toggle, a separate ``SELECT account_locked``.  Scalar subqueries collapse
    all of that into a single statement.
    """
    row = db.fetch_one(
        """
        SELECT
            EXISTS(SELECT 1 FROM admin_users WHERE id = ?) AS target_exists,
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS current_pos,
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS target_pos,
            (SELECT account_locked FROM auth_users WHERE id = ?) AS account_locked
        """,
        (target_admin_id, current_admin_id, target_admin_id, target_admin_id),
    )
    if not row:
        return {
            "target_exists": False,
            "current_pos": 0,
            "target_pos": 0,
            "account_locked": 0,
        }
    if isinstance(row, dict):
        exists = row.get("target_exists", False)
        current_pos = row.get("current_pos") or 0
        target_pos = row.get("target_pos") or 0
        locked = row.get("account_locked") or 0
    else:
        exists = row[0]
        current_pos = row[1] or 0
        target_pos = row[2] or 0
        locked = row[3] or 0
    return {
        "target_exists": bool(exists),
        "current_pos": int(current_pos),
        "target_pos": int(target_pos),
        "account_locked": int(locked),
    }


def _can_manage_admin_at(
    current_admin_id: int, target_admin_id: int, current_pos: int, target_pos: int
) -> bool:
    """Same rule as ``can_manage_admin`` using pre-fetched positions."""
    if current_admin_id == target_admin_id:
        return False
    return current_pos > target_pos


def _load_role_for_delegation(db, name: str, admin_id: int) -> dict:
    """Fetch a role by name plus the actor's position in a single query."""
    row = db.fetch_one(
        """
        SELECT
            (SELECT id FROM admin_roles WHERE name = ?) AS role_id,
            (SELECT position FROM admin_roles WHERE name = ?) AS role_pos,
            (SELECT is_system FROM admin_roles WHERE name = ?) AS role_system,
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS current_pos
        """,
        (name, name, name, admin_id),
    )
    if not row:
        return {
            "role_id": None,
            "role_pos": None,
            "role_system": False,
            "current_pos": 0,
        }
    if isinstance(row, dict):
        role_id = row.get("role_id")
        role_pos = row.get("role_pos")
        role_system = row.get("role_system", False)
        current_pos = row.get("current_pos") or 0
    else:
        role_id = row[0]
        role_pos = row[1] if len(row) > 1 else None
        role_system = row[2] if len(row) > 2 else False
        current_pos = row[3] or 0 if len(row) > 3 else 0
    return {
        "role_id": role_id,
        "role_pos": None if role_pos is None else int(role_pos),
        "role_system": bool(role_system),
        "current_pos": int(current_pos),
    }


def _can_manage_role_at(
    current_pos: int, role_pos: int | None, role_system: bool
) -> bool:
    """Same rule as ``can_manage_role`` using pre-fetched hierarchy."""
    if role_pos is None:
        return False
    if role_system:
        return current_pos >= 100
    return current_pos > role_pos


# Write-time hierarchy guard for admin-account mutations. The handlers above
# authorize against a single read snapshot (``_load_admin_hierarchy``), then
# write in a separate, autocommitted statement. Without re-asserting the
# decision at write time, a concurrent role grant to the target (or revocation
# from the actor) in that window lets the stale, previously-authorized snapshot
# still mutate an admin that is no longer beneath the actor -- the same TOCTOU
# the role routes close with compare-and-swap in their WHERE clauses. These
# subqueries recompute both max positions from the database *as the write
# executes*. Fixed SQL text; every external value stays a bound parameter.
_ACTOR_POSITION_SQL = (
    "(SELECT COALESCE(MAX(r.position), 0) "
    "FROM admin_roles r "
    "JOIN admin_role_assignments a ON r.id = a.role_id "
    "WHERE a.admin_id = ?)"
)


def _hierarchy_write_guard(actor_id: int, target_admin_id: int):
    """Return a predicate + params re-asserting ``actor_pos > target_pos``.

    Prepend ``WHERE id = ?`` (or an equivalent target-id predicate) to the
    caller's write; both position values are then recomputed at write time so a
    promotion/demotion that lands after the read makes the write affect zero
    rows instead of silently succeeding.
    """
    predicate = f"{_ACTOR_POSITION_SQL} > {_ACTOR_POSITION_SQL}"
    return predicate, (actor_id, target_admin_id)


def _write_missed(cursor) -> bool:
    """True when a guarded write matched no row (lost the hierarchy race).

    Drivers that do not report a row count are treated as an unknown outcome
    rather than a failure, so observability-only doubles and backends without
    rowcount support keep their previous behaviour.
    """
    rowcount = getattr(cursor, "rowcount", None)
    return isinstance(rowcount, int) and rowcount <= 0


def _assert_hierarchy_at_write(db, actor_id: int, target_admin_id: int) -> None:
    """Fail closed with 409 if the actor no longer outranks the target.

    A no-op ``UPDATE`` is used as an atomic check because every driver here
    reports matched-row counts for ``UPDATE`` (unlike ``SELECT``). The target's
    presence is already guaranteed by ``WHERE id = ?``.
    """
    guard_sql, guard_params = _hierarchy_write_guard(actor_id, target_admin_id)
    cursor = db.execute(
        f"UPDATE admin_users SET id = id WHERE id = ? AND {guard_sql}",
        (target_admin_id, *guard_params),
    )
    if _write_missed(cursor):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Admin privileges changed concurrently, retry",
                }
            },
        )


class AdminUsersMixin(AdminUsersRouterProtocol):
    async def list_admin_users(
        self,
        request: Request,
        # SECURITY: bound the page so one poll cannot materialise the entire
        # admin roster. Mirrors the limits on the role/admin-role listings.
        limit: int = Query(500, ge=1, le=1000),
        offset: int = Query(0, ge=0, le=1_000_000),
    ):
        """List administrator accounts from the canonical admin tables."""
        check_host_restriction(request)
        require_admin_permission(request, "admin.read")

        try:
            db = self._get_db()
            rows = db.fetch_all(
                _admin_user_query("ORDER BY a.created_at DESC LIMIT ? OFFSET ?"),
                (limit, offset),
            )
            users = [
                AdminUserResponse(
                    id=str(_row_value(row, "id", 0)),
                    username=_required_row_value(row, "username", 1, str),
                    email=_row_value(row, "email", 2, "") or "",
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
                email=_row_value(row, "email", 2, "") or "",
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
            from src.core.admin.permissions import log_admin_action

            requested_role = user_data.role
            role_ctx = _load_role_for_delegation(db, requested_role, admin_id)
            role_id = role_ctx["role_id"]
            role_pos = role_ctx["role_pos"]
            role_system = role_ctx["role_system"]
            if not role_id and requested_role == "admin":
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
                # The freshly inserted built-in role has a known position.
                role_pos = 10
                role_system = False
            if not role_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Requested admin role not found",
                        }
                    },
                )
            role_id = _required_row_value({"id": role_id}, "id", 0, int)
            if not _can_manage_role_at(role_ctx["current_pos"], role_pos, role_system):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot assign this admin role",
                        }
                    },
                )

            # Object-level gate: the position hierarchy is not enough. A
            # low-position role can still carry scopes the actor does not hold;
            # assigning it to a freshly created admin (whose password the actor
            # sets) would launder those privileges. Mirror the check the
            # dedicated /roles/assign route applies to the same concrete role.
            from src.core.admin.permissions import can_grant_existing_role

            if not can_grant_existing_role(db, admin_id, role_id):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot assign a role carrying permissions you do not hold",
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
            hierarchy = _load_admin_hierarchy(db, admin_id, uid)
            if not hierarchy["target_exists"]:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import log_admin_action

            if not _can_manage_admin_at(
                admin_id, uid, hierarchy["current_pos"], hierarchy["target_pos"]
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot update admins at or above your own position",
                        }
                    },
                )

            # Re-assert the hierarchy at write time: a concurrent promotion of
            # the target (or revocation from the actor) after the read above
            # must abort the whole mutation instead of using the stale grant.
            _assert_hierarchy_at_write(db, admin_id, uid)

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
                role_ctx = _load_role_for_delegation(db, requested_role, admin_id)
                if not role_ctx["role_id"]:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {
                                "code": 400,
                                "message": "Requested admin role not found",
                            }
                        },
                    )
                role_id = _required_row_value({"id": role_ctx["role_id"]}, "id", 0, int)
                if not _can_manage_role_at(
                    role_ctx["current_pos"],
                    role_ctx["role_pos"],
                    role_ctx["role_system"],
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Cannot assign this admin role",
                            }
                        },
                    )
                # Same object-level gate as the dedicated role-assignment
                # route: retargeting an admin must not be a laundering path for
                # scopes the actor does not itself hold.
                from src.core.admin.permissions import can_grant_existing_role

                if not can_grant_existing_role(db, admin_id, role_id):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Cannot assign a role carrying permissions you do not hold",
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
                # The admin's effective privileges changed via this alternate
                # role-assignment path too; refresh cached auth context.
                invalidate_privilege_caches()

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
            hierarchy = _load_admin_hierarchy(db, admin_id, uid)
            if not hierarchy["target_exists"]:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import log_admin_action

            if not _can_manage_admin_at(
                admin_id, uid, hierarchy["current_pos"], hierarchy["target_pos"]
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot delete admins at or above your own position",
                        }
                    },
                )
            # Atomic compare-and-swap: re-check the hierarchy as the write
            # executes. A concurrent promotion of the target turns this into a
            # 409 before any of the cascading deletes below run.
            _assert_hierarchy_at_write(db, admin_id, uid)
            # ``users.delete`` is a canonical APPROVAL_REQUIRED_ACTIONS entry
            # (and a default config `require_approval_for` action): deleting an
            # admin account irreversibly erases its role assignments, sessions
            # and auth user. A single admin holding `admin.edit` must not be
            # able to do that in a multi-admin deployment without the
            # documented second-admin approval. Mirror force-purge: gate after
            # the hierarchy checks (so a blocked upward/self delete cannot
            # consume a valid approval) but before any erasure side effect.
            require_approved_request("users.delete")
            db.execute("DELETE FROM admin_role_assignments WHERE admin_id = ?", (uid,))
            db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (uid,))
            db.execute("DELETE FROM admin_users WHERE id = ?", (uid,))
            db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))
            invalidate_privilege_caches()
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
            hierarchy = _load_admin_hierarchy(db, admin_id, uid)
            if not hierarchy["target_exists"]:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Admin user not found"}},
                )

            from src.core.admin.permissions import log_admin_action

            if not _can_manage_admin_at(
                admin_id, uid, hierarchy["current_pos"], hierarchy["target_pos"]
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot toggle status of admins at or above your own position",
                        }
                    },
                )
            current_locked = bool(hierarchy["account_locked"])
            new_status = 0 if current_locked else 1
            # Fold the write-time hierarchy check and a compare-and-swap on the
            # observed lock state into the UPDATE itself. A concurrent promotion
            # of the target (or a second toggle) makes this match zero rows, so
            # neither the account state nor its sessions are changed on a stale
            # authorization decision.
            guard_sql, guard_params = _hierarchy_write_guard(admin_id, uid)
            cursor = db.execute(
                "UPDATE auth_users SET account_locked = ? "
                f"WHERE id = ? AND account_locked = ? AND {guard_sql}",
                (new_status, uid, int(current_locked), *guard_params),
            )
            if _write_missed(cursor):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": 409,
                            "message": "Admin privileges changed concurrently, retry",
                        }
                    },
                )
            if new_status:
                db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (uid,))
            # This writes auth_users directly (bypassing auth.update_user), so
            # without an explicit invalidation a cached TokenInfo still carries
            # the old account_locked flag for the rest of the verify TTL.
            invalidate_privilege_caches()
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
