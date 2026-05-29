from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional, List

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, raise_bad_request, raise_forbidden, raise_internal
from .protocol import DatabaseProtocol

router = APIRouter()


class EnhancedReportRequest(BaseModel):
    target_type: str = Field(..., description="'message' or 'user'")
    target_id: str = Field(..., description="ID of the reported message or user")
    reason: str = Field(..., min_length=1, max_length=1000, description="Report reason")
    category: str = Field("other", description="Report category")
    priority: str = Field("medium", description="Priority: low, medium, high, critical")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    evidence_urls: Optional[List[str]] = Field(
        None, description="URLs of evidence (screenshots, etc.)"
    )
    channel_id: Optional[str] = Field(
        None, description="Channel ID (for message reports)"
    )
    server_id: Optional[str] = Field(
        None, description="Server ID (for message reports)"
    )


class ReportStatusUpdateRequest(BaseModel):
    status: str = Field(
        ...,
        description="New status: open, investigating, resolved, dismissed, escalated",
    )
    priority: Optional[str] = Field(None, description="Updated priority")
    assigned_to: Optional[str] = Field(None, description="Admin user ID to assign")
    admin_notes: Optional[str] = Field(None, max_length=2000, description="Admin notes")
    resolution: Optional[str] = Field(
        None, max_length=2000, description="Resolution description"
    )


@router.post(
    "/reports/enhanced",
    summary="Submit enhanced report",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def submit_enhanced_report(
    body: EnhancedReportRequest, current_user: TokenInfo = Depends(get_current_user)
):
    valid_priorities = {"low", "medium", "high", "critical"}
    if body.priority not in valid_priorities:
        raise_bad_request(
            f"Invalid priority. Must be one of: {', '.join(valid_priorities)}"
        )

    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    import json
    from src.utils.encryption import generate_snowflake_id
    import time

    target_id = parse_id(body.target_id, "target ID")

    now = int(time.time() * 1000)
    report_id = generate_snowflake_id()

    reported_user_id = None
    message_content = None
    channel_id = None
    server_id = None

    if body.target_type == "message":
        channel_id = int(body.channel_id) if body.channel_id else None
        server_id = int(body.server_id) if body.server_id else None
        messaging = api.get_messaging()
        if messaging:
            try:
                msg = messaging.get_message(current_user.user_id, target_id)
                if msg:
                    reported_user_id = msg.author_id
                    message_content = msg.content[:500] if msg.content else None
            except Exception:
                pass
    elif body.target_type == "user":
        reported_user_id = target_id

    evidence_urls_str = json.dumps(body.evidence_urls) if body.evidence_urls else None

    try:
        db.execute(
            """INSERT INTO reports
               (id, reporter_id, report_type, target_id, channel_id, server_id,
                reason, category, details, evidence_ids, message_content,
                reported_user_id, status, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (
                report_id,
                current_user.user_id,
                body.target_type,
                target_id,
                channel_id,
                server_id,
                body.reason,
                body.category,
                body.details,
                evidence_urls_str,
                message_content,
                reported_user_id,
                body.priority,
                now,
                now,
            ),
        )

        logger.info(
            f"Enhanced report {report_id} submitted by user {current_user.user_id}"
        )
        return {
            "success": True,
            "report_id": str(report_id),
            "status": "open",
            "priority": body.priority,
        }
    except Exception as e:
        logger.error(f"Failed to submit enhanced report: {e}")
        raise_internal("Internal server error")


@router.patch(
    "/reports/{report_id}/status",
    summary="Update report status (admin)",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def update_report_status(
    report_id: str,
    body: ReportStatusUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    admin_row = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?",
        (current_user.user_id,),
    )
    if not admin_row:
        raise_forbidden("Admin access required")

    rid = parse_id(report_id, "report ID")

    valid_statuses = {"open", "investigating", "resolved", "dismissed", "escalated"}
    if body.status not in valid_statuses:
        raise_bad_request(
            f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )

    import time

    now = int(time.time() * 1000)

    updates = ["status = ?", "updated_at = ?"]
    params: list = [body.status, now]

    def _has_column(name: str, _db: DatabaseProtocol = db) -> bool:
        try:
            return bool(_db.column_exists("reports", name))
        except Exception:
            return True

    ALLOWED_UPDATE_COLUMNS = {
        "status",
        "updated_at",
        "priority",
        "assigned_to",
        "admin_notes",
        "resolution",
        "resolved_at",
        "resolved_by",
        "escalated_at",
        "evidence_urls",
        "reviewed_at",
        "reviewed_by",
    }

    if body.priority:
        updates.append("priority = ?")
        params.append(body.priority)
    if body.assigned_to:
        updates.append("assigned_to = ?")
        params.append(int(body.assigned_to))
    if body.admin_notes:
        updates.append("admin_notes = ?")
        params.append(body.admin_notes)
    if body.resolution:
        updates.append("resolution = ?")
        params.append(body.resolution)
        if _has_column("resolved_at", db):
            updates.append("resolved_at = ?")
            params.append(now)
        if _has_column("resolved_by", db):
            updates.append("resolved_by = ?")
            params.append(current_user.user_id)
    if body.status == "escalated" and _has_column("escalated_at", db):
        updates.append("escalated_at = ?")
        params.append(now)

    if body.status in ("investigating", "resolved", "dismissed", "escalated"):
        if _has_column("reviewed_at", db):
            updates.append("reviewed_at = ?")
            params.append(now)
        if _has_column("reviewed_by", db):
            updates.append("reviewed_by = ?")
            params.append(current_user.user_id)

    for u in updates:
        col = u.split(" = ")[0].strip()
        if col not in ALLOWED_UPDATE_COLUMNS:
            raise_bad_request(f"Invalid column: {col}")

    params.append(rid)

    try:
        db.execute(
            f"UPDATE reports SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        return {"success": True, "report_id": str(rid), "status": body.status}
    except Exception as e:
        logger.error(f"Failed to update report status: {e}")
        raise_internal("Internal server error")


@router.get(
    "/reports/{report_id}",
    summary="Get report details",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_report_details(
    report_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    rid = parse_id(report_id, "report ID")

    row = db.fetch_one("SELECT * FROM reports WHERE id = ?", (rid,))
    if not row:
        row = db.fetch_one("SELECT * FROM message_reports WHERE id = ?", (rid,))
    if not row:
        row = db.fetch_one("SELECT * FROM user_reports WHERE id = ?", (rid,))
    if not row:
        raise_bad_request("Report not found")

    data = dict(row)
    is_reporter = data.get("reporter_id") == current_user.user_id
    is_admin = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?", (current_user.user_id,)
    )

    if not is_reporter and not is_admin:
        raise_forbidden("Access denied")

    return {"report": data}
