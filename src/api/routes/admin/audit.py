"""
Admin audit log routes for querying and exporting audit records.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional
from datetime import datetime
import csv
import io

from .utils import check_host_restriction
import utils.logger as logger

router = APIRouter()


@router.get("/audit/logs")
async def get_audit_logs(
    request: Request,
    action_family: Optional[str] = Query(None, description="Filter by action family"),
    status: Optional[str] = Query(None, description="Filter by status"),
    admin_id: Optional[int] = Query(None, description="Filter by admin ID"),
    target_type: Optional[str] = Query(None, description="Filter by target type"),
    date_from: Optional[int] = Query(None, description="Start timestamp (ms)"),
    date_to: Optional[int] = Query(None, description="End timestamp (ms)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
):
    """
    Retrieve paginated audit log entries with optional filters.
    """
    check_host_restriction(request)

    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

        query = "SELECT * FROM admin_audit_log"
        conditions = []
        params = []

        if action_family:
            conditions.append("action LIKE ?")
            params.append(f"{action_family}%")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if admin_id:
            conditions.append("admin_id = ?")
            params.append(admin_id)

        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)

        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        # Build a separate count query with the same WHERE conditions
        count_query = "SELECT COUNT(*) as total FROM admin_audit_log"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        total = db.fetch_one(count_query, params)["total"]

        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])

        logs = db.fetch_all(query, params)

        formatted_logs = []
        for log in logs:
            formatted_logs.append(
                {
                    "id": log["id"],
                    "admin_id": log["admin_id"],
                    "action": log["action"],
                    "target_type": log["target_type"],
                    "target_id": log.get("target_id"),
                    "details": log.get("details"),
                    "ip_address": log.get("ip_address"),
                    "user_agent": log.get("user_agent"),
                    "status": log["status"],
                    "created_at": log["created_at"],
                    "timestamp": datetime.fromtimestamp(
                        log["created_at"] / 1000
                    ).isoformat()
                    if log.get("created_at")
                    else None,
                }
            )

        return {
            "logs": formatted_logs,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }
    except Exception as e:
        logger.error(f"Audit logs retrieval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/logs/stats")
async def get_audit_stats(request: Request):
    """
    Retrieve summary statistics for audit logs (today's totals by action family).
    """
    check_host_restriction(request)

    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

        today_start = int(
            datetime.now()
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )

        stats = {
            "total_today": db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ?",
                (today_start,),
            )["count"],
            "success_today": db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND status = 'success'",
                (today_start,),
            )["count"],
            "failed_today": db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND status = 'failed'",
                (today_start,),
            )["count"],
            "by_action_family": {},
        }

        families = [
            "login",
            "user",
            "admin",
            "config",
            "approval",
            "security",
            "plexijoin",
            "license",
        ]
        for family in families:
            count = db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND action LIKE ?",
                (today_start, f"{family}%"),
            )["count"]
            if count > 0:
                stats["by_action_family"][family] = count

        return stats
    except Exception as e:
        logger.error(f"Audit stats retrieval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/logs/export")
async def export_audit_logs(
    request: Request,
    action_family: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    admin_id: Optional[int] = Query(None),
    target_type: Optional[str] = Query(None),
    date_from: Optional[int] = Query(None),
    date_to: Optional[int] = Query(None),
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    """
    Export audit logs as CSV or JSON.
    """
    check_host_restriction(request)

    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

        query = "SELECT * FROM admin_audit_log"
        conditions = []
        params = []

        if action_family:
            conditions.append("action LIKE ?")
            params.append(f"{action_family}%")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if admin_id:
            conditions.append("admin_id = ?")
            params.append(admin_id)

        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)

        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        logs = db.fetch_all(query, params)

        if format == "json":
            from fastapi.responses import JSONResponse

            return JSONResponse(
                content={"logs": [dict(log) for log in logs]},
                headers={"Content-Disposition": "attachment; filename=audit_logs.json"},
            )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "ID",
                "Admin ID",
                "Action",
                "Target Type",
                "Target ID",
                "Details",
                "IP Address",
                "User Agent",
                "Status",
                "Timestamp",
            ]
        )

        for log in logs:
            timestamp = (
                datetime.fromtimestamp(log["created_at"] / 1000).isoformat()
                if log.get("created_at")
                else ""
            )
            writer.writerow(
                [
                    log["id"],
                    log["admin_id"],
                    log["action"],
                    log.get("target_type", ""),
                    log.get("target_id", ""),
                    log.get("details", ""),
                    log.get("ip_address", ""),
                    log.get("user_agent", ""),
                    log["status"],
                    timestamp,
                ]
            )

        output.seek(0)

        from fastapi.responses import StreamingResponse

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
        )
    except Exception as e:
        logger.error(f"Audit export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
