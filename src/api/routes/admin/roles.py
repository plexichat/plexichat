"""
Admin role management routes.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import utils.logger as logger
from fastapi import APIRouter, Request, HTTPException, Query
from starlette.responses import Response
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeID,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import check_host_restriction, require_admin_permission
from src.api.schemas.policy_types import UIText
from src.core.auth.privilege_cache import invalidate_privilege_caches
from src.core.security.hardening import HardeningViolation, parse_id
from src.core.admin.read_cache import (
    ROLE_CATALOG_DEFAULT_LIMIT,
    ROLE_CATALOG_DEFAULT_OFFSET,
    invalidate_admin_read_caches,
    warm_role_catalog,
)
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


# Write-time hierarchy guard. ``update_role``/``delete_role`` re-assert their
# read-time invariants in the WHERE clause, but ``assign_role``/``revoke_role``
# used to grant/revoke privileges off the same snapshot with a completely
# unguarded write. A concurrent admin could promote a role, rewrite its scopes
# or elevate the target admin in that window and the stale, previously-
# authorized decision would still hand out (or strip) privileges. The fragment
# below recomputes both hierarchy decisions from the database *as the write
# executes*, so a stale updater gets a 409 instead of laundering privileges.
# It is fixed SQL text; every external value stays a bound parameter.
_ACTOR_POSITION_SQL = (
    "(SELECT COALESCE(MAX(rp.position), 0) "
    "FROM admin_roles rp "
    "JOIN admin_role_assignments ap ON rp.id = ap.role_id "
    "WHERE ap.admin_id = ?)"
)


def _hierarchy_write_guard(actor_id: int, target_admin_id: int, role_id: int):
    """Build a SQL predicate + params mirroring the read-time hierarchy checks.

    ``_can_manage_admin_at`` becomes ``actor_pos > target_pos`` and
    ``_can_manage_role_at`` becomes ``role_pos < actor_pos`` for ordinary
    roles, with the documented position-100 exception for system roles.
    """
    predicate = (
        "EXISTS (SELECT 1 FROM admin_roles gr "
        "WHERE gr.id = ? "
        f"AND ((gr.is_system = 1 AND {_ACTOR_POSITION_SQL} >= 100) "
        f"OR (gr.is_system = 0 AND gr.position < {_ACTOR_POSITION_SQL}))) "
        "AND EXISTS (SELECT 1 FROM admin_users gu WHERE gu.id = ?) "
        f"AND {_ACTOR_POSITION_SQL} > {_ACTOR_POSITION_SQL}"
    )
    params = (
        role_id,
        actor_id,
        actor_id,
        target_admin_id,
        actor_id,
        target_admin_id,
    )
    return predicate, params


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


def _render_role_catalog(db, limit: int, offset: int) -> bytes:
    """Build the rendered role-catalog payload from ``db``.

    Shared by the request handler and the boot warm-up so the cached bytes are
    exactly what the route would produce. No authorization belongs here: the
    route runs its host/scope gates before consulting the cache, and the
    warm-up calls this with no request context at all.
    """
    from src.core.admin.read_cache import _render_json

    rows = db.fetch_all(
        """
        SELECT id, name, description, permissions, created_at, created_by, updated_at, is_system
        FROM admin_roles
        ORDER BY is_system DESC, name ASC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return _render_json({"roles": [_serialize_role_row(row) for row in rows]})


def warm_admin_permission_catalog_cache() -> None:
    """Prime the expensive first render of the admin role/permission catalog.

    The first ``GET /admin/roles`` after a restart pays for the role query and
    the per-row serialization while holding a request open. Warming that single
    request-invariant page moves the cost onto the boot worker thread. This is
    strictly best-effort: no auth/host gate is skipped, nothing is synthesised
    into a role, and a missing/broken database simply leaves the cache cold so
    the request path builds it on demand exactly as before.
    """
    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            return
        warm_role_catalog(
            lambda: _render_role_catalog(
                db, ROLE_CATALOG_DEFAULT_LIMIT, ROLE_CATALOG_DEFAULT_OFFSET
            )
        )
    except Exception as e:
        logger.debug(f"Admin permission catalog warm-up skipped: {e}")


@asynccontextmanager
async def _role_catalog_lifespan(app: Any):
    # Run off the event loop: the cold DB/file work must not block startup.
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, warm_admin_permission_catalog_cache
        )
    except Exception as e:
        logger.debug(f"Admin permission catalog warm-up failed: {e}")
    yield


# Merged into the application lifespan by FastAPI's include_router().
router.lifespan_context = _role_catalog_lifespan


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
async def list_roles(
    request: Request,
    # SECURITY: bound the page so a single poll cannot render an unbounded
    # role catalog. The default preserves the previous "all roles" response for
    # any realistic deployment; larger pages are rejected, not silently widened.
    limit: int = Query(ROLE_CATALOG_DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(ROLE_CATALOG_DEFAULT_OFFSET, ge=0, le=1_000_000),
):
    """
    List all available admin roles.

    The rendered response is cached briefly (db-scoped, gates first); role
    mutations invalidate it so a stale privilege map is never observed. The
    boot warm-up primes this exact default page, so the first poll is a hit.
    """
    check_host_restriction(request)
    _require_role_manager(request)

    import src.api as api
    from src.core.admin.read_cache import get_or_render

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    body = get_or_render(
        "roles", (limit, offset), lambda: _render_role_catalog(db, limit, offset)
    )
    return Response(content=body, media_type="application/json")


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

    # A new role must appear in the cached catalog immediately, and its
    # creators' auth context must be refreshed like any other role change.
    invalidate_privilege_caches()
    invalidate_admin_read_caches()

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
    invalidate_admin_read_caches()

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
    invalidate_admin_read_caches()

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
    # A role grant is a privilege mutation: bind both caller-supplied ids
    # through the strict ASCII snowflake gate. Bare ``int()`` laundered
    # bool/coercible tokens (``True``->1, ``5.9``->5, ``"١٢٣"``->123,
    # ``"1_2"``->12) into a DIFFERENT admin/role before the INSERT, even
    # though the SnowflakeID schema rejects them on the normal HTTP path; a
    # directly constructed body / internal call bypasses that validation.
    # parse_id rejects every non-int-shaped token and yields a genuine int,
    # failing closed before any assignment write.
    try:
        target_admin_id = parse_id(assignment.admin_id, "admin_id")
        target_role_id = parse_id(assignment.role_id, "role_id")
    except (HardeningViolation, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request data"}},
        )

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

    # SECURITY: the object-level delegation check above used a snapshot of the
    # role's scopes. Bind the write to that exact payload as well as to the
    # current hierarchy, so a concurrent scope escalation cannot turn this
    # assignment into a privilege laundering operation.
    permission_guard_sql = ""
    permission_guard_params: tuple = ()
    if isinstance(raw_permissions, str):
        permission_guard_sql = (
            " AND EXISTS (SELECT 1 FROM admin_roles pr "
            "WHERE pr.id = ? AND pr.permissions = ?)"
        )
        permission_guard_params = (target_role_id, raw_permissions)

    guard_sql, guard_params = _hierarchy_write_guard(
        admin_id, target_admin_id, target_role_id
    )
    cursor = db.execute(
        "INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by) "
        "SELECT ?, ?, ?, ? WHERE " + guard_sql + permission_guard_sql,
        (
            target_admin_id,
            target_role_id,
            now,
            admin_id,
            *guard_params,
            *permission_guard_params,
        ),
    )
    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Role or admin changed concurrently, retry",
                }
            },
        )
    # The target just gained privileges; do not let a cached auth context
    # keep reporting the old (lower) permission set.
    invalidate_privilege_caches()
    invalidate_admin_read_caches()

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

    # SECURITY: same read-then-write TOCTOU as the other role mutations. The
    # DELETE must re-assert the hierarchy that authorized it against the state
    # at write time, otherwise a concurrent promotion could let a stale,
    # previously-authorized snapshot revoke a protected role/assignment.
    guard_sql, guard_params = _hierarchy_write_guard(
        current_admin_id, admin_id, role_id
    )
    cursor = db.execute(
        "DELETE FROM admin_role_assignments "
        "WHERE admin_id = ? AND role_id = ? AND " + guard_sql,
        (admin_id, role_id, *guard_params),
    )
    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Role or admin changed concurrently, retry",
                }
            },
        )
    # Revocation must take effect immediately, including for a session whose
    # verification result is still cached from just before the revoke.
    invalidate_privilege_caches()
    invalidate_admin_read_caches()

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
async def get_admin_roles(
    request: Request,
    admin_id: SnowflakeIDParam,
    # SECURITY: bound the page of assignments returned for one admin.
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=1_000_000),
):
    """
    Get all roles assigned to a specific admin.

    The rendered response is cached briefly (db-scoped, gates first); role
    assignment mutations invalidate it so a revoked role never lingers.
    """
    check_host_restriction(request)
    _require_role_manager(request)

    import src.api as api
    from src.core.admin.read_cache import get_or_render, _render_json

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    def _render() -> bytes:
        rows = db.fetch_all(
            """
            SELECT r.id, r.name, r.description, r.permissions, r.is_system, ra.assigned_at, ra.assigned_by
            FROM admin_role_assignments ra
            JOIN admin_roles r ON ra.role_id = r.id
            WHERE ra.admin_id = ?
            ORDER BY r.name ASC
            LIMIT ? OFFSET ?
            """,
            (admin_id, limit, offset),
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

        return _render_json({"admin_id": str(admin_id), "roles": roles})

    body = get_or_render("admin_roles", (admin_id, limit, offset), _render)
    return Response(content=body, media_type="application/json")
