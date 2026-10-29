"""
Admin approval workflow routes.
"""

from fastapi import APIRouter, Request, HTTPException
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeID,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import (
    check_host_restriction,
    require_admin_permission,
)
from typing import Optional, List
from pydantic import BaseModel, Field
import json
import time

router = APIRouter()


def _parse_approved_by(raw) -> List[int]:
    """Normalize the stored ``approved_by`` value into a list of admin ids.

    Rows written before the JSON-list migration may hold a CSV string
    (``"12,45"``), a bare integer (SQLite dynamic typing on the INTEGER
    column), or NULL. New rows store a JSON array of ints.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        text = str(raw).strip()
        if not text or text == "[]":
            return []
        if text.startswith("["):
            try:
                values = json.loads(text)
            except ValueError:
                values = [p.strip("[] \"'") for p in text.split(",")]
        else:
            values = text.split(",")
    ids: List[int] = []
    for value in values:
        try:
            ids.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return ids


def _serialize_approval_row(row) -> dict:
    """Serialize an ``admin_approvals`` row, stringifying snowflake ids.

    ``id`` (the approval's own snowflake), ``requested_by`` and ``rejected_by``
    (admin ids) are 64-bit snowflakes; served as JSON numbers a JS Number
    rounds them above 2^53. ``approved_by`` is stored as a JSON-encoded string
    of admin ids, so it is already a string.
    """

    def get(key, index):
        if isinstance(row, dict):
            return row.get(key)
        return row[index] if len(row) > index else None

    requested_by = get("requested_by", 1)
    target_id = get("target_id", 4)
    rejected_by = get("rejected_by", 10)
    return {
        "id": str(get("id", 0)),
        "requested_by": str(requested_by) if requested_by is not None else None,
        "action_type": get("action_type", 2),
        "target_type": get("target_type", 3),
        "target_id": str(target_id) if target_id is not None else None,
        "action_details": get("action_details", 5),
        "status": get("status", 6),
        "required_approvals": get("required_approvals", 7),
        "current_approvals": get("current_approvals", 8),
        "approved_by": get("approved_by", 9),
        "rejected_by": str(rejected_by) if rejected_by is not None else None,
        "rejection_reason": get("rejection_reason", 11),
        "expires_at": get("expires_at", 12),
        "created_at": get("created_at", 13),
        "updated_at": get("updated_at", 14),
    }


class ApprovalRequest(BaseModel):
    """Request to create an approval request."""

    action_type: str = Field(..., max_length=100)
    target_type: Optional[str] = Field(None, max_length=50)
    target_id: Optional[SnowflakeID] = None
    action_details: Optional[str] = Field(None, max_length=1000)


class ApprovalDecision(BaseModel):
    """Request to approve or reject an approval request."""

    decision: str = Field(..., pattern="^(approve|reject)$")
    reason: Optional[str] = Field(None, max_length=500)


class ApprovalComment(BaseModel):
    """Add a comment to an approval request."""

    comment: str = Field(..., min_length=1, max_length=1000)


@router.get(
    "/approvals",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
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

    return {"approvals": [_serialize_approval_row(row) for row in rows]}


@router.get(
    "/approvals/{approval_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Approval request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_approval(request: Request, approval_id: SnowflakeIDParam):
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

    return _serialize_approval_row(row)


@router.post(
    "/approvals/request",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def request_approval(request: Request, approval_data: ApprovalRequest):
    """
    Create a new approval request for a sensitive action.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.approvals")

    # Check if action requires approval
    import utils.config as config

    from src.core.admin.permissions import (
        APPROVAL_REQUIRED_ACTIONS,
        is_approval_required_action,
    )

    admin_config = config.get("admin_ui", {})
    approval_config = admin_config.get("approval_workflows", {})

    canonical_action = approval_data.action_type in APPROVAL_REQUIRED_ACTIONS

    if not approval_config.get("enabled", False) and not canonical_action:
        # If approval workflows are disabled, action can proceed directly.
        return SuccessResponse(success=True, message="Approval workflows disabled")

    if not is_approval_required_action(approval_data.action_type, approval_config):
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
            int(approval_data.target_id) if approval_data.target_id else None,
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
        int(approval_data.target_id) if approval_data.target_id else 0,
        {"action_type": approval_data.action_type, "approval_id": str(approval_id)},
        request.client.host if request.client else "unknown",
    )

    # Record as a system alert for the Alerts tab.
    try:
        from src.utils.system_alerts import record_system_alert

        record_system_alert(
            "approval_requested",
            {
                "approval_id": str(approval_id),
                "action_type": approval_data.action_type,
                "requested_by": str(admin_id),
            },
            source="approvals",
            severity="info",
            target_path=f"#approval-{approval_id}",
        )
    except Exception:
        pass

    return SuccessResponse(
        success=True, message=f"Approval request created: {approval_id}"
    )


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Approval request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def approve_request(request: Request, approval_id: SnowflakeIDParam):
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

    max_vote_attempts = 5
    new_status = "pending"
    new_current_approvals = 0
    for _ in range(max_vote_attempts):
        # Get approval request
        approval = db.fetch_one(
            """
            SELECT id, requested_by, status, current_approvals, required_approvals, approved_by, expires_at
            FROM admin_approvals
            WHERE id = ?
        """,
            (approval_id,),
        )

        if not approval:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {"code": 404, "message": "Approval request not found"}
                },
            )

        if isinstance(approval, dict):
            status = approval["status"]
            required_approvals = approval["required_approvals"]
            requested_by = approval["requested_by"]
            raw_approved_by = approval["approved_by"]
            expires_at = approval.get("expires_at")
        else:
            status = approval[2]
            required_approvals = approval[4]
            requested_by = approval[1]
            raw_approved_by = approval[5]
            expires_at = approval[6] if len(approval) > 6 else None

        # Check if admin is the requester (self-approval not allowed)
        if admin_id == requested_by:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {"code": 403, "message": "Cannot approve your own request"}
                },
            )

        # Check if approval is still pending and within its configured
        # lifetime. Expired requests cannot be used to authorize actions.
        if expires_at is not None and int(expires_at) <= int(time.time() * 1000):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Approval request has expired"}
                },
            )
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

        approved_by_list = _parse_approved_by(raw_approved_by)

        # SECURITY: exact-membership duplicate check. The previous substring
        # test (``str(admin_id) in approved_by_str``) told admin 12 they had
        # already voted whenever admin 112 had.
        if admin_id in approved_by_list:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Already approved by this admin",
                    }
                },
            )

        now = int(time.time() * 1000)

        # Record the vote as a JSON list of admin ids.
        approved_by_list.append(admin_id)
        new_approved_by = json.dumps(approved_by_list)

        # SECURITY: single conditional UPDATE makes the increment atomic.
        # The CAS guard on ``approved_by`` detects a concurrent voter that
        # read the same snapshot; on rowcount 0 we re-read and retry rather
        # than clobbering their vote or over-counting consensus.
        cursor = db.execute(
            """
            UPDATE admin_approvals
            SET current_approvals = current_approvals + 1,
                approved_by = ?,
                status = CASE
                    WHEN current_approvals + 1 >= required_approvals THEN 'approved'
                    ELSE 'pending'
                END,
                updated_at = ?
            WHERE id = ?
              AND status = 'pending'
              AND (
                    approved_by = ?
                    OR (approved_by IS NULL AND CAST(? AS TEXT) IS NULL)
                  )
        """,
            (new_approved_by, now, approval_id, raw_approved_by, raw_approved_by),
        )

        if getattr(cursor, "rowcount", 0) > 0:
            break

        # Lost the race: another vote or a status transition landed first.
        continue
    else:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Approval request changed concurrently, retry",
                }
            },
        )

    updated = db.fetch_one(
        "SELECT status, current_approvals, required_approvals FROM admin_approvals"
        " WHERE id = ?",
        (approval_id,),
    )
    if isinstance(updated, dict):
        new_status = updated["status"]
        new_current_approvals = updated["current_approvals"]
        required_approvals = updated["required_approvals"]
    elif updated:
        new_status = updated[0]
        new_current_approvals = updated[1]
        required_approvals = updated[2]

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

    # Record as a system alert for the Alerts tab.
    # Only emit "approval_approved" when the threshold is reached;
    # intermediate votes use "approval_voted".
    try:
        from src.utils.system_alerts import record_system_alert

        if new_status == "approved":
            record_system_alert(
                "approval_approved",
                {
                    "approval_id": str(approval_id),
                    "approved_by": str(admin_id),
                    "current_approvals": new_current_approvals,
                    "required_approvals": required_approvals,
                },
                source="approvals",
                severity="info",
                target_path=f"#approval-{approval_id}",
            )
        else:
            record_system_alert(
                "approval_voted",
                {
                    "approval_id": str(approval_id),
                    "voted_by": str(admin_id),
                    "current_approvals": new_current_approvals,
                    "required_approvals": required_approvals,
                },
                source="approvals",
                severity="info",
                target_path=f"#approval-{approval_id}",
            )
    except Exception:
        pass

    return SuccessResponse(success=True, message="Approval recorded")


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Approval request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def reject_request(
    request: Request,
    approval_id: SnowflakeIDParam,
    decision: ApprovalDecision,
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
        SELECT id, status, requested_by, expires_at
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

    # Expired requests cannot be rejected or otherwise mutated.
    expires_at = (
        approval.get("expires_at")
        if isinstance(approval, dict)
        else (approval[3] if len(approval) > 3 else None)
    )
    if expires_at is not None and int(expires_at) <= int(time.time() * 1000):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Approval request has expired"}},
        )

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

    # Record as a system alert for the Alerts tab.
    try:
        from src.utils.system_alerts import record_system_alert

        record_system_alert(
            "approval_rejected",
            {
                "approval_id": str(approval_id),
                "rejected_by": str(admin_id),
                "reason": decision.reason,
            },
            source="approvals",
            severity="warning",
            target_path=f"#approval-{approval_id}",
        )
    except Exception:
        pass

    return SuccessResponse(success=True, message="Approval request rejected")


@router.delete(
    "/approvals/{approval_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Approval request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def cancel_approval(request: Request, approval_id: SnowflakeIDParam):
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
        SELECT id, status, requested_by, expires_at
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


@router.post(
    "/approvals/{approval_id}/comments",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Approval request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_approval_comment(
    request: Request,
    approval_id: SnowflakeIDParam,
    body: ApprovalCommentModel,
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


@router.get(
    "/approvals/{approval_id}/comments",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Approval request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_approval_comments(request: Request, approval_id: SnowflakeIDParam):
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

    # The echoed approval id, each comment's own snowflake id, and its author
    # admin id must all be strings for the same 2^53 reason.
    return {
        "approval_id": str(approval_id),
        "comments": [
            {
                "id": str(c["id"]),
                "admin_id": str(c["admin_id"]) if c["admin_id"] is not None else None,
                "comment": c["comment"],
                "created_at": c["created_at"],
            }
            for c in comments
        ],
    }
