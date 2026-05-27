"""
Admin role management routes.
"""

from fastapi import APIRouter, Request, HTTPException
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
from typing import Optional
from pydantic import BaseModel, Field

router = APIRouter()


class RoleCreate(BaseModel):
    """Request to create a new admin role."""

    name: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., max_length=500)
    permissions: dict = Field(...)


class RoleUpdate(BaseModel):
    """Request to update an admin role."""

    description: Optional[str] = Field(None, max_length=500)
    permissions: Optional[dict] = None


class RoleAssignment(BaseModel):
    """Request to assign a role to an admin."""

    admin_id: int
    role_id: int


@router.get("/roles")
async def list_roles(request: Request):
    """
    List all available admin roles.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.roles", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    rows = db.fetch_all("""
        SELECT id, name, description, permissions, created_at, created_by, updated_at, is_system
        FROM admin_roles
        ORDER BY is_system DESC, name ASC
    """)

    roles = []
    for row in rows:
        if isinstance(row, dict):
            roles.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "permissions": row["permissions"],
                    "created_at": row["created_at"],
                    "created_by": row["created_by"],
                    "updated_at": row.get("updated_at"),
                    "is_system": bool(row["is_system"]),
                }
            )
        else:
            roles.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "permissions": row[3],
                    "created_at": row[4],
                    "created_by": row[5],
                    "updated_at": row[6] if len(row) > 6 else None,
                    "is_system": bool(row[7]) if len(row) > 7 else False,
                }
            )

    return {"roles": roles}


@router.get("/roles/{role_id}")
async def get_role(request: Request, role_id: int):
    """
    Get details of a specific role.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.roles", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
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

    if isinstance(row, dict):
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "permissions": row["permissions"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "updated_at": row.get("updated_at"),
            "is_system": bool(row["is_system"]),
        }
    else:
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "permissions": row[3],
            "created_at": row[4],
            "created_by": row[5],
            "updated_at": row[6] if len(row) > 6 else None,
            "is_system": bool(row[7]) if len(row) > 7 else False,
        }


@router.post("/roles", response_model=SuccessResponse)
async def create_role(request: Request, role_data: RoleCreate):
    """
    Create a new admin role.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.roles", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    import time
    import json
    from src.utils.encryption import generate_snowflake_id

    now = int(time.time() * 1000)
    role_id = generate_snowflake_id()

    # Validate permissions JSON
    try:
        permissions_json = json.dumps(role_data.permissions)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid permissions format"}},
        )

    # Check duplicate name
    existing = db.fetch_one(
        "SELECT id FROM admin_roles WHERE name = ?", (role_data.name,)
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Role name already exists"}},
        )

    db.execute(
        """
        INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """,
        (
            role_id,
            role_data.name,
            role_data.description,
            permissions_json,
            now,
            admin_id,
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


@router.put("/roles/{role_id}", response_model=SuccessResponse)
async def update_role(request: Request, role_id: int, role_data: RoleUpdate):
    """
    Update an existing admin role.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.roles", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    # Check if role exists and is not a system role
    role = db.fetch_one("SELECT is_system FROM admin_roles WHERE id = ?", (role_id,))
    if not role:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    is_system = bool(role["is_system"] if isinstance(role, dict) else role[0])
    if is_system:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Cannot modify system roles"}},
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
        try:
            permissions_json = json.dumps(role_data.permissions)
            updates.append("permissions = ?")
            params.append(permissions_json)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid permissions format"}
                },
            )

    params.append(int(role_id))

    db.execute(
        f"""
        UPDATE admin_roles
        SET {", ".join(updates)}
        WHERE id = ?
    """,
        params,
    )

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


@router.delete("/roles/{role_id}", response_model=SuccessResponse)
async def delete_role(request: Request, role_id: int):
    """
    Delete an admin role.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.roles", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    # Check if role exists and is not a system role
    role = db.fetch_one("SELECT is_system FROM admin_roles WHERE id = ?", (role_id,))
    if not role:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    is_system = bool(role["is_system"] if isinstance(role, dict) else role[0])
    if is_system:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Cannot delete system roles"}},
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

    db.execute("DELETE FROM admin_roles WHERE id = ?", (int(role_id),))

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


@router.post("/roles/assign", response_model=SuccessResponse)
async def assign_role(request: Request, assignment: RoleAssignment):
    """
    Assign a role to an admin.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.roles", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    import time

    now = int(time.time() * 1000)

    # Check if role exists
    role = db.fetch_one(
        "SELECT id FROM admin_roles WHERE id = ?", (assignment.role_id,)
    )
    if not role:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Role not found"}},
        )

    # Check if admin exists
    admin = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?", (assignment.admin_id,)
    )
    if not admin:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Admin not found"}},
        )

    # Check if assignment already exists
    existing = db.fetch_one(
        "SELECT admin_id FROM admin_role_assignments WHERE admin_id = ? AND role_id = ?",
        (assignment.admin_id, assignment.role_id),
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
        (assignment.admin_id, assignment.role_id, now, admin_id),
    )

    # Log the action
    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "role.assign",
        "admin_user",
        assignment.admin_id,
        {"role_id": assignment.role_id},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Role assigned successfully")


@router.delete("/roles/{admin_id}/{role_id}", response_model=SuccessResponse)
async def revoke_role(request: Request, admin_id: int, role_id: int):
    """
    Revoke a role from an admin.
    """
    check_host_restriction(request)
    current_admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(current_admin_id, "admin.roles.assign", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
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


@router.get("/admins/{admin_id}/roles")
async def get_admin_roles(request: Request, admin_id: int):
    """
    Get all roles assigned to a specific admin.
    """
    check_host_restriction(request)
    current_admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(current_admin_id, "admin.roles.read", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
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
            roles.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "permissions": row["permissions"],
                    "is_system": bool(row["is_system"]),
                    "assigned_at": row["assigned_at"],
                    "assigned_by": row["assigned_by"],
                }
            )
        else:
            roles.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "permissions": row[3],
                    "is_system": bool(row[4]),
                    "assigned_at": row[5],
                    "assigned_by": row[6],
                }
            )

    return {"admin_id": admin_id, "roles": roles}
