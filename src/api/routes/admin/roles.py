"""
Admin role management routes.
"""

import json

from fastapi import APIRouter, Request, HTTPException
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeID,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import check_host_restriction, require_admin_permission
from src.api.schemas.policy_types import UIText
from src.core.auth.privilege_cache import invalidate_privilege_caches
from typing import Optional
from pydantic import BaseModel, Field

router = APIRouter()

# Single source of truth for the scope that guards every role-management
# route.  Handlers must funnel through ``_require_role_manager`` so the gate
# cannot silently drift (or be omitted) as routes are added or edited.
ROLE_MANAGE_SCOPE = "admin.roles"


def _require_role_manager(request: Request) -> int:
    """Authenticate the caller and require the role-management scope.

    Returns the acting admin id.  Every handler in this module calls this
    helper as its first authorization step; the regression suite enumerates
    the router to enforce that invariant.
    """
    return require_admin_permission(request, ROLE_MANAGE_SCOPE)


# ---------------------------------------------------------------------------
# Batched hierarchy lookups
#
# The role routes need three pieces of hierarchy information before they can
# authorize a mutation: the actor's max role position, the target admin's max
# role position, and the target role's position/system flag.  The shared
# ``can_manage_admin``/``can_manage_role`` helpers fetch each of those
# independently (and ``get_admin_max_position`` for the actor twice on the
# assign path).  The helpers below resolve the whole set in ONE query while
# reproducing those helpers' decisions exactly, so the authorization gate
# stays identical minus the round trips.
# ---------------------------------------------------------------------------


def _load_hierarchy(
    db, current_admin_id: int, target_admin_id: int, role_id: int
) -> dict:
    """Resolve every hierarchy input for a role mutation in one query.

    Returns ``current_pos``, ``target_pos``, ``admin_exists``, ``role_pos``
    (``None`` when the role does not exist), ``role_is_system`` and the raw
    ``role_permissions`` payload (used by the object-level grant check).
    Scalar subqueries keep this a single statement regardless of how many
    roles or admins exist.
    """
    row = db.fetch_one(
        """
        SELECT
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS current_pos,
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS target_pos,
            EXISTS(SELECT 1 FROM admin_users WHERE id = ?) AS admin_exists,
            (SELECT position FROM admin_roles WHERE id = ?) AS role_pos,
            (SELECT is_system FROM admin_roles WHERE id = ?) AS role_is_system,
            (SELECT permissions FROM admin_roles WHERE id = ?) AS role_permissions
        """,
        (
            current_admin_id,
            target_admin_id,
            target_admin_id,
            role_id,
            role_id,
            role_id,
        ),
    )
    if not row:
        return {
            "current_pos": 0,
            "target_pos": 0,
            "admin_exists": False,
            "role_pos": None,
            "role_is_system": False,
            "role_permissions": None,
        }
    if isinstance(row, dict):
        current_pos = row.get("current_pos") or 0
        target_pos = row.get("target_pos") or 0
        admin_exists = row.get("admin_exists", False)
        role_pos = row.get("role_pos")
        role_is_system = row.get("role_is_system", False)
        role_permissions = row.get("role_permissions")
    else:
        current_pos = row[0] or 0
        target_pos = row[1] or 0
        admin_exists = row[2]
        role_pos = row[3] if len(row) > 3 else None
        role_is_system = row[4] if len(row) > 4 else False
        role_permissions = row[5] if len(row) > 5 else None
    return {
        "current_pos": int(current_pos),
        "target_pos": int(target_pos),
        "admin_exists": bool(admin_exists),
        "role_pos": None if role_pos is None else int(role_pos),
        "role_is_system": bool(role_is_system),
        "role_permissions": role_permissions,
    }


def _can_manage_admin_at(
    current_admin_id: int, target_admin_id: int, current_pos: int, target_pos: int
) -> bool:
    """Same rule as ``can_manage_admin`` but using pre-fetched positions."""
    if current_admin_id == target_admin_id:
        return False
    return current_pos > target_pos


def _can_manage_role_at(
    current_pos: int, role_pos: int | None, role_is_system: bool
) -> bool:
    """Same rule as ``can_manage_role`` but using pre-fetched hierarchy."""
    if role_pos is None:
        return False
    if role_is_system:
        return current_pos >= 100
    return current_pos > role_pos


def _load_create_role_context(db, admin_id: int, name: str) -> tuple[int, bool]:
    """Fetch the actor's max position and role-name collision in one query."""
    row = db.fetch_one(
        """
        SELECT
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS max_pos,
            EXISTS(SELECT 1 FROM admin_roles WHERE name = ?) AS name_taken
        """,
        (admin_id, name),
    )
    if not row:
        return 0, False
    if isinstance(row, dict):
        return int(row.get("max_pos") or 0), bool(row.get("name_taken", False))
    return int(row[0] or 0), bool(row[1])


def _serialize_role_row(row) -> dict:
    """Serialize an ``admin_roles`` row, stringifying its snowflake ids.

    Role ids and the creating admin's id are snowflakes (see
    ``generate_snowflake_id``); a JS Number cannot hold a 64-bit id exactly,
    so emitting them as numbers would round them in the admin UI.
    """
    if isinstance(row, dict):
        created_by = row.get("created_by")
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "permissions": row["permissions"],
            "created_at": row["created_at"],
            "created_by": str(created_by) if created_by is not None else None,
            "updated_at": row.get("updated_at"),
            "is_system": bool(row["is_system"]),
        }
    created_by = row[5] if len(row) > 5 else None
    return {
        "id": str(row[0]),
        "name": row[1],
        "description": row[2],
        "permissions": row[3],
        "created_at": row[4],
        "created_by": str(created_by) if created_by is not None else None,
        "updated_at": row[6] if len(row) > 6 else None,
        "is_system": bool(row[7]) if len(row) > 7 else False,
    }


class RoleCreate(BaseModel):
    """Request to create a new admin role."""

    name: UIText = Field(..., min_length=1, max_length=50)
    description: str = Field(..., max_length=500)
    permissions: dict = Field(...)
    position: Optional[int] = Field(None, ge=0, le=99)


class RoleUpdate(BaseModel):
    """Request to update an admin role."""

    description: Optional[str] = Field(None, max_length=500)
    permissions: Optional[dict] = None


class RoleAssignment(BaseModel):
    """Request to assign a role to an admin."""

    admin_id: SnowflakeID
    role_id: SnowflakeID


@router.get(
    "/roles",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_roles(request: Request):
    """
    List all available admin roles.
    """
    check_host_restriction(request)
    _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    rows = db.fetch_all("""
        SELECT id, name, description, permissions, created_at, created_by, updated_at, is_system
        FROM admin_roles
        ORDER BY is_system DESC, name ASC
    """)

    return {"roles": [_serialize_role_row(row) for row in rows]}


@router.get(
    "/roles/{role_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_role(request: Request, role_id: SnowflakeIDParam):
    """
    Get details of a specific role.
    """
    check_host_restriction(request)
    _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    row = db.fetch_one(
        """
        SELECT id, name, description, permissions, created_at, created_by, updated_at, is_system
        FROM admin_roles
        WHERE id = ?
    """,
        (role_id,),
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    return _serialize_role_row(row)


@router.post(
    "/roles",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_role(request: Request, role_data: RoleCreate):
    """
    Create a new admin role.
    """
    check_host_restriction(request)
    admin_id = _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    import time
    import json
    from src.utils.encryption import generate_snowflake_id

    now = int(time.time() * 1000)
    role_id = generate_snowflake_id()

    # Only documented, non-wildcard scopes may be delegated. The closed
    # vocabulary and wildcard rejection live in the core permission module.
    from src.core.admin.permissions import (
        can_delegate_permissions,
        validate_delegable_permissions,
    )

    try:
        permissions = validate_delegable_permissions(role_data.permissions)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(exc)}},
        )

    # Scope hierarchy: never let a caller grant privileges it does not hold.
    if not can_delegate_permissions(db, admin_id, permissions):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot grant permissions you do not hold",
                }
            },
        )

    # One query supplies both the actor's position and the duplicate-name
    # signal that is consumed further down (same validation order as before).
    actor_position, name_taken = _load_create_role_context(db, admin_id, role_data.name)
    requested_position = (
        actor_position - 1 if role_data.position is None else role_data.position
    )
    if (
        actor_position <= 0
        or requested_position < 0
        or requested_position >= actor_position
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Role position must be below your own",
                }
            },
        )

    try:
        permissions_json = json.dumps(permissions)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid permissions format"}},
        )

    # Duplicate name (pre-fetched with the position above).
    if name_taken:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Role name already exists"}},
        )

    db.execute(
        """
        INSERT INTO admin_roles
            (id, name, description, permissions, created_at, created_by, is_system, position)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    """,
        (
            role_id,
            role_data.name,
            role_data.description,
            permissions_json,
            now,
            admin_id,
            requested_position,
        ),
    )

    # Log the action
    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "role.create",
        "admin_role",
        role_id,
        {"name": role_data.name, "description": role_data.description},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Role updated successfully")


@router.put(
    "/roles/{role_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_role(
    request: Request, role_id: SnowflakeIDParam, role_data: RoleUpdate
):
    """
    Update an existing admin role.
    """
    check_host_restriction(request)
    admin_id = _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Check role existence, system flag and hierarchy in one query.
    hierarchy = _load_hierarchy(db, admin_id, admin_id, role_id)
    if hierarchy["role_pos"] is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    if hierarchy["role_is_system"]:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Cannot modify system roles"}},
        )

    # Enforce role hierarchy: can only modify roles at a lower position.
    if not _can_manage_role_at(
        hierarchy["current_pos"], hierarchy["role_pos"], hierarchy["role_is_system"]
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot manage roles at or above your own position",
                }
            },
        )

    import time
    import json

    now = int(time.time() * 1000)
    updates = ["updated_at = ?"]
    params: list = [now]

    if role_data.description is not None:
        updates.append("description = ?")
        params.append(role_data.description)

    if role_data.permissions is not None:
        from src.core.admin.permissions import (
            can_delegate_permissions,
            validate_delegable_permissions,
        )

        try:
            permissions = validate_delegable_permissions(role_data.permissions)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(exc)}},
            )
        # Scope hierarchy applies to updates as well as creation: editing a
        # low-position role must not become a privilege-escalation backdoor.
        if not can_delegate_permissions(db, admin_id, permissions):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Cannot grant permissions you do not hold",
                    }
                },
            )
        try:
            permissions_json = json.dumps(permissions)
            updates.append("permissions = ?")
            params.append(permissions_json)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid permissions format"}
                },
            )

    # SECURITY: the hierarchy gate above reads the role's position/system flag
    # and then writes in a separate statement. Without a guard on the write, a
    # concurrent admin could promote the role to a system role (or raise its
    # position) between our read and this UPDATE, and we would still modify a
    # now-protected role. Re-check the exact invariants that authorized the
    # mutation in the WHERE clause so the write is a compare-and-swap against
    # the observed state; a stale updater gets 409 instead of a silent clobber.
    params.append(int(role_id))
    params.append(hierarchy["current_pos"])

    # updates list contains only hardcoded column assignments (safe from injection)
    template = (
        "UPDATE admin_roles SET {assignments} "
        "WHERE id = ? AND is_system = 0 AND position < ?"
    )
    query = template.format(assignments=", ".join(updates))
    cursor = db.execute(query, params)
    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Role changed concurrently, retry",
                }
            },
        )

    # Editing a role's scopes changes the effective privileges of every
    # holder.  Drop the cached auth context so a bearer/cookie token cannot
    # keep answering with the pre-edit permission map.
    invalidate_privilege_caches()

    # Log the action
    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "role.update",
        "admin_role",
        role_id,
        {
            "description": role_data.description,
            "permissions_updated": role_data.permissions is not None,
        },
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Role updated successfully")


@router.delete(
    "/roles/{role_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_role(request: Request, role_id: SnowflakeIDParam):
    """
    Delete an admin role.
    """
    check_host_restriction(request)
    admin_id = _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Check role existence, system flag and hierarchy in one query.
    hierarchy = _load_hierarchy(db, admin_id, admin_id, role_id)
    if hierarchy["role_pos"] is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    if hierarchy["role_is_system"]:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Cannot delete system roles"}},
        )

    # Enforce role hierarchy: can only delete roles at a lower position.
    if not _can_manage_role_at(
        hierarchy["current_pos"], hierarchy["role_pos"], hierarchy["role_is_system"]
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot manage roles at or above your own position",
                }
            },
        )

    # Check if role is assigned to any admins
    assignments = db.fetch_one(
        "SELECT COUNT(*) as count FROM admin_role_assignments WHERE role_id = ?",
        (role_id,),
    )
    assignment_count = (
        assignments["count"] if isinstance(assignments, dict) else assignments[0]
    )

    if assignment_count > 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "Cannot delete role that is assigned to admins",
                }
            },
        )

    # SECURITY: same read-then-write TOCTOU as update_role. The DELETE must
    # re-assert the invariants that authorized it, otherwise a concurrent
    # promotion of the role to system (or above the actor) would still delete
    # a now-protected role using our stale, previously-authorized snapshot.
    cursor = db.execute(
        "DELETE FROM admin_roles WHERE id = ? AND is_system = 0 AND position < ?",
        (int(role_id), hierarchy["current_pos"]),
    )
    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Role changed concurrently, retry",
                }
            },
        )
    invalidate_privilege_caches()

    # Log the action
    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "role.delete",
        "admin_role",
        int(role_id),
        {},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Role deleted successfully")


@router.post(
    "/roles/assign",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Role or admin not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def assign_role(request: Request, assignment: RoleAssignment):
    """
    Assign a role to an admin.
    """
    check_host_restriction(request)
    admin_id = _require_role_manager(request)
    target_admin_id = int(assignment.admin_id)
    target_role_id = int(assignment.role_id)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    import time

    now = int(time.time() * 1000)

    # Role existence, admin existence and both hierarchy positions in one
    # query.  This replaces five separate lookups on the assign path (role
    # row, admin row, actor position twice, target position, role hierarchy).
    hierarchy = _load_hierarchy(db, admin_id, target_admin_id, target_role_id)
    if hierarchy["role_pos"] is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    if not hierarchy["admin_exists"]:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Admin not found"}},
        )

    # Enforce hierarchy: can only assign roles if you can manage the target admin AND the role
    if not _can_manage_admin_at(
        admin_id,
        target_admin_id,
        hierarchy["current_pos"],
        hierarchy["target_pos"],
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot assign roles to admins at or above your own position",
                }
            },
        )
    if not _can_manage_role_at(
        hierarchy["current_pos"], hierarchy["role_pos"], hierarchy["role_is_system"]
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot assign roles at or above your own position",
                }
            },
        )

    # Object-level check on the concrete role being granted: holding
    # ``admin.roles`` and outranking the role is not enough if that role
    # carries scopes the actor itself does not hold.  The role's permission
    # payload came back with the hierarchy query, so only the actor's held
    # scopes still need resolving (same decision as can_grant_existing_role).
    from src.core.admin.permissions import can_delegate_permissions

    raw_permissions = hierarchy["role_permissions"]
    try:
        role_permissions = (
            json.loads(raw_permissions)
            if isinstance(raw_permissions, str)
            else raw_permissions
        )
    except (TypeError, ValueError):
        role_permissions = None
    if not isinstance(role_permissions, dict) or not can_delegate_permissions(
        db, admin_id, role_permissions
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot assign a role carrying permissions you do not hold",
                }
            },
        )

    # Check if assignment already exists
    existing = db.fetch_one(
        "SELECT admin_id FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
        (target_admin_id, target_role_id),
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": "Role already assigned to this admin"}
            },
        )

    db.execute(
        """
        INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by)
        VALUES (?, ?, ?, ?)
    """,
        (target_admin_id, target_role_id, now, admin_id),
    )
    # The target just gained privileges; do not let a cached auth context
    # keep reporting the old (lower) permission set.
    invalidate_privilege_caches()

    # Log the action
    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "role.assign",
        "admin_user",
        target_admin_id,
        {"role_id": target_role_id},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Role assigned successfully")


@router.delete(
    "/roles/{admin_id}/{role_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Role assignment not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_role(
    request: Request, admin_id: SnowflakeIDParam, role_id: SnowflakeIDParam
):
    """
    Revoke a role from an admin.
    """
    check_host_restriction(request)
    current_admin_id = _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Enforce hierarchy: can only revoke roles if you can manage the target
    # admin AND the role.  All inputs are resolved in a single query.
    hierarchy = _load_hierarchy(db, current_admin_id, admin_id, role_id)
    if not _can_manage_admin_at(
        current_admin_id,
        admin_id,
        hierarchy["current_pos"],
        hierarchy["target_pos"],
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot revoke roles from admins at or above your own position",
                }
            },
        )
    if not _can_manage_role_at(
        hierarchy["current_pos"], hierarchy["role_pos"], hierarchy["role_is_system"]
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Cannot revoke roles at or above your own position",
                }
            },
        )

    # Check if assignment exists
    existing = db.fetch_one(
        "SELECT admin_id FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
        (admin_id, role_id),
    )
    if not existing:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role assignment not found"}},
        )

    db.execute(
        "DELETE FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
        (admin_id, role_id),
    )
    # Revocation must take effect immediately, including for a session whose
    # verification result is still cached from just before the revoke.
    invalidate_privilege_caches()

    # Log the action
    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        current_admin_id,
        "role.revoke",
        "admin_user",
        admin_id,
        {"role_id": role_id},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Role revoked successfully")


@router.get(
    "/admins/{admin_id}/roles",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_admin_roles(request: Request, admin_id: SnowflakeIDParam):
    """
    Get all roles assigned to a specific admin.
    """
    check_host_restriction(request)
    _require_role_manager(request)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    rows = db.fetch_all(
        """
        SELECT r.id, r.name, r.description, r.permissions, r.is_system, ra.assigned_at, ra.assigned_by
        FROM admin_role_assignments ra
        JOIN admin_roles r ON ra.role_id = r.id
        WHERE ra.admin_id = ?
        ORDER BY r.name ASC
    """,
        (admin_id,),
    )

    roles = []
    for row in rows:
        if isinstance(row, dict):
            assigned_by = row.get("assigned_by")
            roles.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "description": row["description"],
                    "permissions": row["permissions"],
                    "is_system": bool(row["is_system"]),
                    "assigned_at": row["assigned_at"],
                    "assigned_by": (
                        str(assigned_by) if assigned_by is not None else None
                    ),
                }
            )
        else:
            assigned_by = row[6] if len(row) > 6 else None
            roles.append(
                {
                    "id": str(row[0]),
                    "name": row[1],
                    "description": row[2],
                    "permissions": row[3],
                    "is_system": bool(row[4]),
                    "assigned_at": row[5],
                    "assigned_by": (
                        str(assigned_by) if assigned_by is not None else None
                    ),
                }
            )

    return {"admin_id": str(admin_id), "roles": roles}
