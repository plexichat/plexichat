"""
Admin approval workflow routes.
"""

from fastapi import APIRouter, Request, HTTPException
from src.api.schemas.common import SuccessResponse
from .utils import (
    check_host_restriction,
    get_admin_from_token,
    require_admin_permission,
)
from typing import Optional
from pydantic import BaseModel, Field
import time

router = APIRouter()


class ApprovalRequest(BaseModel):
    """Request to create an approval request."""

    action_type: str = Field(..., max_length=100)
    target_type: Optional[str] = Field(None, max_length=50)
    target_id: Optional[int] = None
    action_details: Optional[str] = Field(None, max_length=1000)


class ApprovalDecision(BaseModel):
    """Request to approve or reject an approval request."""

    decision: str = Field(..., pattern="^(approve|reject)$")
    reason: Optional[str] = Field(None, max_length=500)


class ApprovalComment(BaseModel):
    """Add a comment to an approval request."""

    comment: str = Field(..., min_length=1, max_length=1000)


@router.get("/approvals")
async def list_approvals(
    request: Request, status: Optional[str] = None, action_type: Optional[str] = None
):
    """
    List approval requests with optional filtering.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Build query with filters
    query = """
        SELECT id, requested_by, action_type, target_type, target_id, action_details, 
               status, required_approvals, current_approvals, approved_by, rejected_by, 
               rejection_reason, expires_at, created_at, updated_at
        FROM admin_approvals
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if action_type:
        query += " AND action_type = ?"
        params.append(action_type)

    query += " ORDER BY created_at DESC LIMIT 100"

    rows = db.fetch_all(query, params)

    approvals = []
    for row in rows:
        if isinstance(row, dict):
            approvals.append(
                {
                    "id": row["id"],
                    "requested_by": row["requested_by"],
                    "action_type": row["action_type"],
                    "target_type": row["target_type"],
                    "target_id": row["target_id"],
                    "action_details": row["action_details"],
                    "status": row["status"],
                    "required_approvals": row["required_approvals"],
                    "current_approvals": row["current_approvals"],
                    "approved_by": row["approved_by"],
                    "rejected_by": row["rejected_by"],
                    "rejection_reason": row["rejection_reason"],
                    "expires_at": row["expires_at"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        else:
            approvals.append(
                {
                    "id": row[0],
                    "requested_by": row[1],
                    "action_type": row[2],
                    "target_type": row[3],
                    "target_id": row[4],
                    "action_details": row[5],
                    "status": row[6],
                    "required_approvals": row[7],
                    "current_approvals": row[8],
                    "approved_by": row[9],
                    "rejected_by": row[10],
                    "rejection_reason": row[11],
                    "expires_at": row[12],
                    "created_at": row[13],
                    "updated_at": row[14],
                }
            )

    return {"approvals": approvals}


@router.get("/approvals/{approval_id}")
async def get_approval(request: Request, approval_id: int):
    """
    Get details of a specific approval request.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    row = db.fetch_one(
        """
        SELECT id, requested_by, action_type, target_type, target_id, action_details, 
               status, required_approvals, current_approvals, approved_by, rejected_by, 
               rejection_reason, expires_at, created_at, updated_at
        FROM admin_approvals
        WHERE id = ?
    """,
        (approval_id,),
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Approval request not found"}},
        )

    if isinstance(row, dict):
        return {
            "id": row["id"],
            "requested_by": row["requested_by"],
            "action_type": row["action_type"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "action_details": row["action_details"],
            "status": row["status"],
            "required_approvals": row["required_approvals"],
            "current_approvals": row["current_approvals"],
            "approved_by": row["approved_by"],
            "rejected_by": row["rejected_by"],
            "rejection_reason": row["rejection_reason"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    else:
        return {
            "id": row[0],
            "requested_by": row[1],
            "action_type": row[2],
            "target_type": row[3],
            "target_id": row[4],
            "action_details": row[5],
            "status": row[6],
            "required_approvals": row[7],
            "current_approvals": row[8],
            "approved_by": row[9],
            "rejected_by": row[10],
            "rejection_reason": row[11],
            "expires_at": row[12],
            "created_at": row[13],
            "updated_at": row[14],
        }


@router.post("/approvals/request", response_model=SuccessResponse)
async def request_approval(request: Request, approval_data: ApprovalRequest):
    """
    Create a new approval request for a sensitive action.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check if action requires approval
    import utils.config as config

    admin_config = config.get("admin_ui", {})
    approval_config = admin_config.get("approval_workflows", {})

    if not approval_config.get("enabled", False):
        # If approval workflows are disabled, action can proceed directly
        return SuccessResponse(success=True, message="Approval workflows disabled")

    require_approval_for = approval_config.get("require_approval_for", [])
    if approval_data.action_type not in require_approval_for:
        # Action doesn't require approval
        return SuccessResponse(success=True, message="Action does not require approval")

    # Check for single admin bypass
    single_admin_bypass = approval_config.get("single_admin_bypass", True)
    if single_admin_bypass:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        admin_count = db.fetch_one("SELECT COUNT(*) as count FROM admin_users")
        count = (
            admin_count["count"] if isinstance(admin_count, dict) else admin_count[0]
        )

        if count == 1:
            return SuccessResponse(success=True, message="Single admin bypass")

    # Create approval request
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    from src.utils.encryption import generate_snowflake_id

    now = int(time.time() * 1000)
    approval_id = generate_snowflake_id()

    # Calculate expiration
    approval_timeout_hours = approval_config.get("approval_timeout_hours", 48)
    expires_at = now + (approval_timeout_hours * 3600 * 1000)

    required_approvals = approval_config.get("approval_required_admins", 2)

    db.execute(
        """
        INSERT INTO admin_approvals 
        (id, requested_by, action_type, target_type, target_id, action_details, 
         status, required_approvals, current_approvals, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?)
    """,
        (
            approval_id,
            admin_id,
            approval_data.action_type,
            approval_data.target_type,
            approval_data.target_id,
            approval_data.action_details,
            required_approvals,
            expires_at,
            now,
            now,
        ),
    )

    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "approval.request",
        approval_data.target_type or "system",
        approval_data.target_id or 0,
        {"action_type": approval_data.action_type, "approval_id": str(approval_id)},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(
        success=True, message=f"Approval request created: {approval_id}"
    )


@router.post("/approvals/{approval_id}/approve", response_model=SuccessResponse)
async def approve_request(request: Request, approval_id: int):
    """
    Approve an approval request.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Get approval request
    approval = db.fetch_one(
        """
        SELECT id, requested_by, status, current_approvals, required_approvals, approved_by
        FROM admin_approvals
        WHERE id = ?
    """,
        (approval_id,),
    )

    if not approval:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Approval request not found"}},
        )

    if isinstance(approval, dict):
        status = approval["status"]
        current_approvals = approval["current_approvals"]
        required_approvals = approval["required_approvals"]
        requested_by = approval["requested_by"]
        approved_by_str = approval["approved_by"] or ""
    else:
        status = approval[2]
        current_approvals = approval[3]
        required_approvals = approval[4]
        requested_by = approval[1]
        approved_by_str = approval[5] or ""

    # Check if already approved by this admin
    if str(admin_id) in approved_by_str:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": "Already approved by this admin"}
            },
        )

    # Check if approval is still pending
    if status != "pending":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Approval request is {status}, cannot approve",
                }
            },
        )

    # Check if admin is the requester (self-approval not allowed)
    if admin_id == requested_by:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": 403, "message": "Cannot approve your own request"}
            },
        )

    now = int(time.time() * 1000)

    # Add admin to approved_by list
    approved_by_list = approved_by_str.split(",") if approved_by_str else []
    approved_by_list.append(str(admin_id))
    new_approved_by = ",".join(approved_by_list)
    new_current_approvals = current_approvals + 1

    # Check if required approvals reached
    new_status = (
        "approved" if new_current_approvals >= required_approvals else "pending"
    )

    db.execute(
        """
        UPDATE admin_approvals
        SET current_approvals = ?, approved_by = ?, status = ?, updated_at = ?
        WHERE id = ?
    """,
        (new_current_approvals, new_approved_by, new_status, now, approval_id),
    )

    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "approval.approve",
        "approval",
        approval_id,
        {
            "current_approvals": new_current_approvals,
            "required_approvals": required_approvals,
            "approval_id": str(approval_id),
        },
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Approval recorded")


@router.post("/approvals/{approval_id}/reject", response_model=SuccessResponse)
async def reject_request(
    request: Request, approval_id: int, decision: ApprovalDecision
):
    """
    Reject an approval request.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Get approval request
    approval = db.fetch_one(
        """
        SELECT id, status, requested_by
        FROM admin_approvals
        WHERE id = ?
    """,
        (approval_id,),
    )

    if not approval:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Approval request not found"}},
        )

    if isinstance(approval, dict):
        status = approval["status"]
    else:
        status = approval[1]

    # Check if approval is still pending
    if status != "pending":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Approval request is {status}, cannot reject",
                }
            },
        )

    now = int(time.time() * 1000)

    db.execute(
        """
        UPDATE admin_approvals
        SET status = 'rejected', rejected_by = ?, rejection_reason = ?, updated_at = ?
        WHERE id = ?
    """,
        (admin_id, decision.reason, now, approval_id),
    )

    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "approval.reject",
        "approval",
        approval_id,
        {"reason": decision.reason, "approval_id": str(approval_id)},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Approval request rejected")


@router.delete("/approvals/{approval_id}", response_model=SuccessResponse)
async def cancel_approval(request: Request, approval_id: int):
    """
    Cancel a pending approval request. Only the requester or a super admin can cancel.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    approval = db.fetch_one(
        """
        SELECT id, status, requested_by
        FROM admin_approvals
        WHERE id = ?
    """,
        (approval_id,),
    )

    if not approval:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Approval request not found"}},
        )

    if isinstance(approval, dict):
        status = approval["status"]
        requested_by = approval["requested_by"]
    else:
        status = approval[1]
        requested_by = approval[2]

    if status != "pending":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Approval request is {status}, cannot cancel",
                }
            },
        )

    # Only the requester or super admin can cancel
    if admin_id != requested_by:
        from src.core.admin.permissions import check_permission

        perm = check_permission(admin_id, "*", db)
        if not perm.has_permission:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Only the requester or a super admin can cancel this request",
                    }
                },
            )

    now = int(time.time() * 1000)

    db.execute(
        """
        UPDATE admin_approvals
        SET status = 'cancelled', updated_at = ?
        WHERE id = ?
    """,
        (now, approval_id),
    )

    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "approval.cancel",
        "approval",
        approval_id,
        {"action": "cancel", "approval_id": str(approval_id)},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Approval request cancelled")


class ApprovalCommentModel(BaseModel):
    """Comment content for an approval request."""

    comment: str = Field(..., min_length=1, max_length=1000)


@router.post("/approvals/{approval_id}/comments", response_model=SuccessResponse)
async def add_approval_comment(
    request: Request, approval_id: int, body: ApprovalCommentModel
):
    """
    Add a comment to an approval request for discussion.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Verify approval exists
    approval = db.fetch_one(
        "SELECT id FROM admin_approvals WHERE id = ?", (approval_id,)
    )
    if not approval:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Approval request not found"}},
        )

    from src.utils.encryption import generate_snowflake_id

    now = int(time.time() * 1000)
    comment_id = generate_snowflake_id()

    db.execute(
        """
        INSERT INTO admin_approval_comments (id, approval_id, admin_id, comment, created_at)
        VALUES (?, ?, ?, ?, ?)
    """,
        (comment_id, approval_id, admin_id, body.comment, now),
    )

    from src.core.admin.permissions import log_admin_action

    log_admin_action(
        db,
        admin_id,
        "approval.comment",
        "approval",
        approval_id,
        {"comment": body.comment[:100], "approval_id": str(approval_id)},
        request.client.host if request.client else "unknown",
    )

    return SuccessResponse(success=True, message="Comment added")


@router.get("/approvals/{approval_id}/comments")
async def get_approval_comments(request: Request, approval_id: int):
    """
    Get all comments for an approval request.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.approvals")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    comments = db.fetch_all(
        """
        SELECT id, approval_id, admin_id, comment, created_at
        FROM admin_approval_comments
        WHERE approval_id = ?
        ORDER BY created_at ASC
    """,
        (approval_id,),
    )

    return {
        "approval_id": approval_id,
        "comments": [
            {
                "id": c["id"],
                "admin_id": c["admin_id"],
                "comment": c["comment"],
                "created_at": c["created_at"],
            }
            for c in comments
        ],
    }
