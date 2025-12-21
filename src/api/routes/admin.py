"""
Admin API routes.

Provides admin-only endpoints for server management.
Host-restricted by default to localhost only.

SECURITY WARNING: Disabling host_restriction allows ANYONE to access the admin
panel and view potentially sensitive user data including feedback, telemetry,
and system statistics. Only disable this if you have other security measures
in place (VPN, firewall, reverse proxy auth, etc.)
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import time

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_db


router = APIRouter(tags=["admin"])


class AdminLoginRequest(BaseModel):
    """Admin login request."""
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class OTPVerifyRequest(BaseModel):
    """OTP verification request."""
    admin_id: str  # String to avoid JavaScript precision loss with large integers
    code: str = Field(..., min_length=6, max_length=8)
    is_setup: bool = False


class TicketStatusUpdate(BaseModel):
    """Update ticket status."""
    status: str = Field(..., pattern="^(open|in_progress|resolved|closed)$")


class InternalNoteCreate(BaseModel):
    """Create internal note."""
    content: str = Field(..., min_length=1, max_length=2000)


class HashReportReviewRequest(BaseModel):
    """Review a hash report."""
    action: str = Field(..., pattern="^(block|clear|dismiss)$")
    notes: Optional[str] = Field(None, max_length=2000)


class ManualBlockHashRequest(BaseModel):
    """Manually block a hash."""
    hash_value: str = Field(..., min_length=64, max_length=128)
    reason: str = Field(..., min_length=1, max_length=500)


class TicketResponse(BaseModel):
    """Feedback ticket response."""
    id: int
    user_id: int
    username: str
    content: str
    category: Optional[str]
    rating: Optional[int]
    status: str
    created_at: int
    resolved_at: Optional[int]
    resolved_by: Optional[int]


class NoteResponse(BaseModel):
    """Admin note response."""
    id: int
    ticket_id: int
    admin_id: int
    admin_username: str
    content: str
    created_at: int


def _check_host_restriction(request: Request) -> None:
    """Check if client IP is allowed to access admin UI."""
    admin_config = config.get("admin_ui", {})

    if not admin_config.get("enabled", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        allowed_hosts = host_restriction.get("allowed_hosts", ["127.0.0.1", "localhost", "::1"])
        client_ip = request.client.host if request.client else "unknown"

        from src.core import admin
        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Check origin if allowed_origins is configured
    allowed_origins = admin_config.get("allowed_origins", [])
    if allowed_origins:
        origin = request.headers.get("origin", "")
        if origin and origin not in allowed_origins:
            logger.warning(f"Admin access denied - origin {origin} not in allowed_origins")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed")


def _get_admin_from_token(request: Request) -> int:
    """Get admin ID from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    token = auth_header[7:]
    from src.core import admin
    admin_id = admin.validate_session(token)

    if not admin_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    return admin_id


# ==================== Auth Routes ====================

@router.post("/login")
async def admin_login(
    request: Request,
    login_data: AdminLoginRequest,
    db = Depends(get_db)
):
    """Admin login endpoint."""
    _check_host_restriction(request)

    from src.core import admin

    client_ip = request.client.host if request.client else "unknown"
    result = admin.login(login_data.username, login_data.password, client_ip)

    if not result.success:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result.error)

    # If token is returned directly (OTP not required), login is complete
    if result.token:
        return {"status": "success", "token": result.token}

    if result.requires_otp_setup:
        return {
            "status": "otp_setup_required",
            "admin_id": str(result.user_id),  # String to avoid JS precision loss
            "otp_secret": result.otp_secret,
            "otp_qr_uri": result.otp_qr_uri,
            "message": "Scan the QR code with your authenticator app, then enter the code"
        }

    if result.requires_otp_verify:
        return {
            "status": "otp_required",
            "admin_id": str(result.user_id),  # String to avoid JS precision loss
            "message": "Enter your 2FA code"
        }

    return {"status": "success", "token": result.token}


@router.post("/verify-otp")
async def verify_otp(
    request: Request,
    otp_data: OTPVerifyRequest,
    db = Depends(get_db)
):
    """Verify OTP code for admin login."""
    _check_host_restriction(request)

    from src.core import admin

    # Convert string admin_id to int
    try:
        admin_id = int(otp_data.admin_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid admin_id")

    if otp_data.is_setup:
        result = admin.verify_otp_setup(admin_id, otp_data.code)
    else:
        result = admin.verify_otp(admin_id, otp_data.code)

    if not result.success:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result.error)

    return {"status": "success", "token": result.token}


@router.post("/logout")
async def admin_logout(request: Request, db = Depends(get_db)):
    """Admin logout endpoint."""
    _check_host_restriction(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        from src.core import admin
        admin.logout(token)

    return {"success": True}


# ==================== Dashboard Routes ====================

@router.get("/dashboard")
async def get_dashboard(request: Request, db = Depends(get_db)):
    """Get admin dashboard data."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin

    ticket_counts = admin.get_ticket_counts()

    telemetry_stats = []
    try:
        from src.core import telemetry
        if telemetry.is_setup():
            stats = telemetry.get_endpoint_stats(hours=24)
            telemetry_stats = [
                {
                    "endpoint": s.endpoint,
                    "method": s.method,
                    "count": s.count,
                    "avg_ms": round(s.avg_response_time_ms, 2),
                    "p95_ms": round(s.p95_response_time_ms, 2),
                    "error_rate": round(s.error_rate * 100, 2)
                }
                for s in stats[:20]
            ]
    except Exception:
        pass

    return {"tickets": ticket_counts, "telemetry": telemetry_stats}


@router.get("/tickets", response_model=List[TicketResponse])
async def get_tickets(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db = Depends(get_db)
):
    """Get feedback tickets."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin
    tickets = admin.get_feedback_tickets(status_filter, limit, offset)

    return [
        TicketResponse(
            id=t.id, user_id=t.user_id, username=t.username,
            content=t.content, category=t.category, rating=t.rating,
            status=t.status, created_at=t.created_at,
            resolved_at=t.resolved_at, resolved_by=t.resolved_by
        )
        for t in tickets
    ]


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(ticket_id: int, request: Request, db = Depends(get_db)):
    """Get a single ticket."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin
    ticket = admin.get_ticket(ticket_id)

    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    return TicketResponse(
        id=ticket.id, user_id=ticket.user_id, username=ticket.username,
        content=ticket.content, category=ticket.category, rating=ticket.rating,
        status=ticket.status, created_at=ticket.created_at,
        resolved_at=ticket.resolved_at, resolved_by=ticket.resolved_by
    )


@router.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: int,
    update: TicketStatusUpdate,
    request: Request,
    db = Depends(get_db)
):
    """Update ticket status."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    if not admin.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    admin.update_ticket_status(ticket_id, update.status, admin_id)
    logger.info(f"Admin {admin_id} updated ticket {ticket_id} status to {update.status}")

    return {"success": True, "status": update.status}


@router.get("/tickets/{ticket_id}/notes", response_model=List[NoteResponse])
async def get_ticket_notes(ticket_id: int, request: Request, db = Depends(get_db)):
    """Get internal notes for a ticket."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin

    if not admin.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    notes = admin.get_ticket_notes(ticket_id)

    return [
        NoteResponse(
            id=n.id, ticket_id=n.ticket_id, admin_id=n.admin_id,
            admin_username=n.admin_username, content=n.content, created_at=n.created_at
        )
        for n in notes
    ]


@router.post("/tickets/{ticket_id}/notes", response_model=NoteResponse)
async def add_ticket_note(
    ticket_id: int,
    note: InternalNoteCreate,
    request: Request,
    db = Depends(get_db)
):
    """Add internal note to a ticket."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    if not admin.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    new_note = admin.add_internal_note(ticket_id, admin_id, note.content)
    logger.info(f"Admin {admin_id} added note to ticket {ticket_id}")

    if new_note is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create note")

    return NoteResponse(
        id=new_note.id, ticket_id=new_note.ticket_id, admin_id=new_note.admin_id,
        admin_username=new_note.admin_username, content=new_note.content,
        created_at=new_note.created_at
    )


@router.get("/telemetry/stats")
async def get_telemetry_stats(
    request: Request,
    hours: int = 24,
    endpoint: Optional[str] = None,
    source: Optional[str] = None,
    db = Depends(get_db)
):
    """
    Get telemetry statistics.
    
    Args:
        hours: Number of hours to look back (default 24)
        endpoint: Optional endpoint pattern to filter by
        source: Optional source filter - "server" for server-side only, "client" for client-side only
    """
    _check_host_restriction(request)
    _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            return {"stats": [], "message": "Telemetry not initialized"}

        # Map source to client_id filter
        client_id_filter = None
        if source == "server":
            client_id_filter = "server"
        elif source == "client":
            # For client, we want everything except server
            # This requires a different approach - for now, just return all
            pass

        stats = telemetry.get_endpoint_stats(
            hours=hours,
            endpoint_filter=endpoint,
            client_id_filter=client_id_filter
        )

        # Normalize emoji endpoints for display
        import urllib.parse

        return {
            "stats": [
                {
                    "endpoint": urllib.parse.unquote(s.endpoint),
                    "method": s.method,
                    "count": s.count,
                    "avg_ms": round(s.avg_response_time_ms, 2),
                    "min_ms": round(s.min_response_time_ms, 2),
                    "max_ms": round(s.max_response_time_ms, 2),
                    "p50_ms": round(s.p50_response_time_ms, 2),
                    "p95_ms": round(s.p95_response_time_ms, 2),
                    "p99_ms": round(s.p99_response_time_ms, 2),
                    "error_rate": round(s.error_rate * 100, 2)
                }
                for s in stats
            ],
            "source": source or "all"
        }
    except ImportError:
        return {"stats": [], "message": "Telemetry module not available"}


@router.get("/telemetry/history")
async def get_telemetry_history(
    request: Request,
    endpoint: str,
    method: str = "GET",
    hours: int = 24,
    bucket_minutes: int = 5,
    db = Depends(get_db)
):
    """Get telemetry history for an endpoint."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            return {"history": [], "message": "Telemetry not initialized"}

        history = telemetry.get_response_time_history(
            endpoint=endpoint,
            method=method.upper(),
            hours=hours,
            bucket_minutes=bucket_minutes
        )

        return {"history": history}
    except ImportError:
        return {"history": [], "message": "Telemetry module not available"}


@router.post("/telemetry/reset")
async def reset_telemetry_stats(request: Request, db = Depends(get_db)):
    """Reset all telemetry statistics."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            return {"success": False, "message": "Telemetry not initialized"}

        deleted_count = telemetry.reset_all_stats()
        logger.info(f"Admin {admin_id} reset telemetry stats, deleted {deleted_count} records")

        return {"success": True, "deleted_count": deleted_count}
    except ImportError:
        return {"success": False, "message": "Telemetry module not available"}


@router.get("/telemetry/export")
async def export_telemetry_stats(
    request: Request,
    format: str = "json",
    hours: int = 24,
    db = Depends(get_db)
):
    """
    Export telemetry statistics in various formats.
    
    Args:
        format: Export format - "json", "html", "txt", or "csv"
        hours: Number of hours to include
    """
    _check_host_restriction(request)
    _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            raise HTTPException(status_code=500, detail="Telemetry not initialized")

        stats = telemetry.get_endpoint_stats(hours=hours)
        export_time = time.strftime('%Y-%m-%d %H:%M:%S')

        if format == "json":
            return {
                "export_time": export_time,
                "hours": hours,
                "stats": [
                    {
                        "endpoint": s.endpoint,
                        "method": s.method,
                        "count": s.count,
                        "avg_ms": round(s.avg_response_time_ms, 2),
                        "min_ms": round(s.min_response_time_ms, 2),
                        "max_ms": round(s.max_response_time_ms, 2),
                        "p50_ms": round(s.p50_response_time_ms, 2),
                        "p95_ms": round(s.p95_response_time_ms, 2),
                        "p99_ms": round(s.p99_response_time_ms, 2),
                        "error_rate": round(s.error_rate * 100, 2)
                    }
                    for s in stats
                ]
            }

        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Endpoint", "Method", "Count", "Avg (ms)", "Min (ms)", "Max (ms)", "P50 (ms)", "P95 (ms)", "P99 (ms)", "Error %"])
            for s in stats:
                writer.writerow([
                    s.endpoint, s.method, s.count,
                    round(s.avg_response_time_ms, 2),
                    round(s.min_response_time_ms, 2),
                    round(s.max_response_time_ms, 2),
                    round(s.p50_response_time_ms, 2),
                    round(s.p95_response_time_ms, 2),
                    round(s.p99_response_time_ms, 2),
                    round(s.error_rate * 100, 2)
                ])
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.csv"}
            )

        elif format == "txt":
            lines = [
                "PlexiChat Telemetry Report",
                f"Generated: {export_time}",
                f"Time Range: Last {hours} hours",
                "",
                f"{'Endpoint':<50} {'Method':<8} {'Count':>8} {'Avg':>10} {'P95':>10} {'Error%':>8}",
                f"{'-'*50} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*8}",
            ]
            for s in stats:
                lines.append(
                    f"{s.endpoint[:50]:<50} {s.method:<8} {s.count:>8} {s.avg_response_time_ms:>9.1f}ms {s.p95_response_time_ms:>9.1f}ms {s.error_rate*100:>7.1f}%"
                )

            # Summary
            if stats:
                total_requests = sum(s.count for s in stats)
                avg_latency = sum(s.avg_response_time_ms * s.count for s in stats) / total_requests if total_requests else 0
                avg_error = sum(s.error_rate * s.count for s in stats) / total_requests * 100 if total_requests else 0
                lines.extend([
                    "",
                    "Summary:",
                    f"  Total Requests: {total_requests:,}",
                    f"  Average Latency: {avg_latency:.1f}ms",
                    f"  Average Error Rate: {avg_error:.1f}%",
                    f"  Endpoints Tracked: {len(stats)}",
                ])

            return Response(
                content="\n".join(lines),
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.txt"}
            )

        elif format == "html":
            # Generate HTML report
            total_requests = sum(s.count for s in stats) if stats else 0
            avg_latency = sum(s.avg_response_time_ms * s.count for s in stats) / total_requests if total_requests else 0
            avg_error = sum(s.error_rate * s.count for s in stats) / total_requests * 100 if total_requests else 0

            rows_html = ""
            for s in stats:
                latency_class = "good" if s.avg_response_time_ms < 100 else "warn" if s.avg_response_time_ms < 500 else "bad"
                error_class = "good" if s.error_rate < 0.01 else "warn" if s.error_rate < 0.05 else "bad"
                rows_html += f"""
                <tr>
                    <td>{s.endpoint}</td>
                    <td>{s.method}</td>
                    <td>{s.count:,}</td>
                    <td class="{latency_class}">{s.avg_response_time_ms:.1f}</td>
                    <td>{s.min_response_time_ms:.1f}</td>
                    <td>{s.max_response_time_ms:.1f}</td>
                    <td>{s.p50_response_time_ms:.1f}</td>
                    <td class="{latency_class}">{s.p95_response_time_ms:.1f}</td>
                    <td>{s.p99_response_time_ms:.1f}</td>
                    <td class="{error_class}">{s.error_rate*100:.1f}%</td>
                </tr>"""

            html = f"""<!DOCTYPE html>
<html>
<head>
    <title>PlexiChat Telemetry Report - {export_time}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eaeaea; padding: 20px; }}
        h1 {{ color: #e94560; }}
        .summary {{ display: flex; gap: 20px; margin: 20px 0; flex-wrap: wrap; }}
        .stat {{ background: #16213e; padding: 15px 25px; border-radius: 8px; border: 1px solid #0f3460; }}
        .stat h3 {{ font-size: 12px; color: #888; margin: 0 0 5px 0; text-transform: uppercase; }}
        .stat .value {{ font-size: 24px; font-weight: bold; color: #e94560; }}
        table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; margin-top: 20px; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #0f3460; }}
        th {{ background: #0f3460; font-size: 12px; text-transform: uppercase; }}
        .good {{ color: #4ade80; }}
        .warn {{ color: #fbbf24; }}
        .bad {{ color: #ef4444; }}
        @media print {{ body {{ background: white; color: black; }} table {{ background: white; }} th {{ background: #eee; }} }}
    </style>
</head>
<body>
    <h1>PlexiChat Telemetry Report</h1>
    <p>Generated: {export_time} | Time Range: Last {hours} hours</p>
    <div class="summary">
        <div class="stat"><h3>Total Requests</h3><div class="value">{total_requests:,}</div></div>
        <div class="stat"><h3>Avg Latency</h3><div class="value">{avg_latency:.0f}ms</div></div>
        <div class="stat"><h3>Avg Error Rate</h3><div class="value">{avg_error:.1f}%</div></div>
        <div class="stat"><h3>Endpoints</h3><div class="value">{len(stats)}</div></div>
    </div>
    <table>
        <thead>
            <tr><th>Endpoint</th><th>Method</th><th>Count</th><th>Avg (ms)</th><th>Min</th><th>Max</th><th>P50</th><th>P95</th><th>P99</th><th>Error %</th></tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
</body>
</html>"""
            return Response(
                content=html,
                media_type="text/html",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.html"}
            )

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {format}. Use json, csv, txt, or html")

    except ImportError:
        raise HTTPException(status_code=500, detail="Telemetry module not available")


# ==================== Hash Reports Routes ====================

@router.get("/hash-reports")
async def get_hash_reports(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db = Depends(get_db)
):
    """Get hash reports for admin review with image preview support."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin
    reports = admin.get_hash_reports(status_filter, limit, offset)

    return [
        {
            "id": r.id,
            "hash_value": r.hash_value,
            "phash_value": r.phash_value,
            "reporter_id": r.reporter_id,
            "reporter_username": r.reporter_username,
            "reason": r.reason,
            "details": r.details,
            "status": r.status,
            "reported_at": r.reported_at,
            "reviewed_at": r.reviewed_at,
            "reviewed_by": r.reviewed_by,
            "admin_notes": r.admin_notes,
            "uploader_id": r.uploader_id,
            "message_id": r.message_id,
            "attachment_url": r.attachment_url,
            "block_uploader": r.block_uploader
        }
        for r in reports
    ]


@router.get("/hash-reports/counts")
async def get_hash_report_counts(request: Request, db = Depends(get_db)):
    """Get counts of hash reports by status."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin
    return admin.get_hash_report_counts()


@router.post("/hash-reports/{report_id}/review")
async def review_hash_report(
    report_id: int,
    review: HashReportReviewRequest,
    request: Request,
    db = Depends(get_db)
):
    """Review a hash report (block, clear, or dismiss)."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    success = admin.review_hash_report(report_id, admin_id, review.action, review.notes)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    logger.info(f"Admin {admin_id} reviewed hash report {report_id}: {review.action}")

    return {"success": True, "action": review.action}


@router.get("/blocked-hashes")
async def get_blocked_hashes(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    db = Depends(get_db)
):
    """Get list of blocked hashes."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin
    hashes = admin.get_blocked_hashes(limit, offset)

    return [
        {
            "hash_value": h.hash_value,
            "reason": h.reason,
            "blocked_at": h.blocked_at,
            "blocked_by": h.blocked_by,
            "auto_blocked": h.auto_blocked,
            "hash_type": h.hash_type,
            "phash_threshold": h.phash_threshold
        }
        for h in hashes
    ]


@router.post("/blocked-hashes")
async def block_hash_manually(
    block_request: ManualBlockHashRequest,
    request: Request,
    db = Depends(get_db)
):
    """Manually block a hash (SHA-256 or pHash)."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    # Determine hash type based on length (pHash is typically 16 chars, SHA-256 is 64)
    hash_type = "phash" if len(block_request.hash_value) <= 32 else "sha256"
    phash_threshold = 10 if hash_type == "phash" else 0

    success = admin.block_hash(
        block_request.hash_value,
        block_request.reason,
        admin_id,
        hash_type=hash_type,
        phash_threshold=phash_threshold
    )

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to block hash")

    logger.info(f"Admin {admin_id} manually blocked {hash_type} hash {block_request.hash_value[:16]}...")

    return {"success": True, "hash_value": block_request.hash_value, "hash_type": hash_type}


@router.delete("/blocked-hashes/{hash_value}")
async def unblock_hash(
    hash_value: str,
    request: Request,
    db = Depends(get_db)
):
    """Unblock a hash."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    success = admin.unblock_hash(hash_value)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unblock hash")

    logger.info(f"Admin {admin_id} unblocked hash {hash_value[:16]}...")

    return {"success": True}


# ==================== Blocked Users (Media Uploads) ====================

class BlockUserRequest(BaseModel):
    """Block a user from uploading media."""
    user_id: int = Field(..., description="User ID to block")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for blocking")
    duration_hours: Optional[int] = Field(None, description="Duration in hours (None = permanent)")


@router.get("/blocked-users")
async def get_blocked_users(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    db = Depends(get_db)
):
    """Get list of users blocked from uploading media."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import admin
    users = admin.get_blocked_users(limit, offset)

    return [
        {
            "user_id": u.user_id,
            "username": u.username,
            "reason": u.reason,
            "blocked_at": u.blocked_at,
            "blocked_by": u.blocked_by,
            "expires_at": u.expires_at
        }
        for u in users
    ]


@router.post("/blocked-users")
async def block_user(
    block_request: BlockUserRequest,
    request: Request,
    db = Depends(get_db)
):
    """Block a user from uploading media."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    success = admin.block_user(
        block_request.user_id,
        block_request.reason,
        admin_id,
        block_request.duration_hours
    )

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to block user")

    logger.info(f"Admin {admin_id} blocked user {block_request.user_id} from uploads")

    return {"success": True, "user_id": block_request.user_id}


@router.delete("/blocked-users/{user_id}")
async def unblock_user(
    user_id: int,
    request: Request,
    db = Depends(get_db)
):
    """Unblock a user from uploading media."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    success = admin.unblock_user(user_id)

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unblock user")

    logger.info(f"Admin {admin_id} unblocked user {user_id} from uploads")

    return {"success": True}


# ==================== User Tier Management ====================

class UserTierUpdate(BaseModel):
    """Update user tier."""
    tier: str = Field(..., pattern="^(free|alpha|beta|premium|staff)$")


class UserSearchResponse(BaseModel):
    """User search result."""
    id: str
    username: str
    email: Optional[str]
    tier: str
    badges: List[str]
    created_at: int


@router.get("/users/search")
async def admin_user_search(
    q: str,
    request: Request,
    limit: int = 20,
    offset: int = 0,
    db = Depends(get_db)
):
    """Search users by username or ID."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    if not q or len(q) < 2:
        return {"users": []}

    # Search by username or ID
    try:
        # Try to parse as ID first
        user_id = int(q)
        rows = db.fetch_all(
            """SELECT id, username, email, tier, badges, created_at 
               FROM auth_users WHERE id = ? LIMIT ?""",
            (user_id, limit)
        )
    except ValueError:
        # Search by username
        rows = db.fetch_all(
            """SELECT id, username, email, tier, badges, created_at 
               FROM auth_users WHERE username LIKE ? LIMIT ?""",
            (f"%{q}%", limit)
        )

    users = []
    for row in rows:
        if isinstance(row, dict):
            badges = row.get("badges", "") or ""
            users.append({
                "id": str(row["id"]),
                "username": row["username"],
                "email": row.get("email"),
                "tier": row.get("tier", "free"),
                "badges": badges.split(",") if badges else [],
                "created_at": row["created_at"]
            })
        else:
            badges = row[4] or ""
            users.append({
                "id": str(row[0]),
                "username": row[1],
                "email": row[2],
                "tier": row[3] or "free",
                "badges": badges.split(",") if badges else [],
                "created_at": row[5]
            })

    return {"users": users}


@router.get("/users/{user_id}")
async def get_user_details(
    user_id: int,
    request: Request,
    db = Depends(get_db)
):
    """Get user details by ID."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    row = db.fetch_one(
        """SELECT id, username, email, tier, badges, created_at, last_login
           FROM auth_users WHERE id = ?""",
        (uid,)
    )

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    if isinstance(row, dict):
        badges = row.get("badges", "") or ""
        return {
            "id": str(row["id"]),
            "username": row["username"],
            "email": row.get("email"),
            "tier": row.get("tier", "free"),
            "badges": badges.split(",") if badges else [],
            "created_at": row["created_at"],
            "last_login": row.get("last_login")
        }
    else:
        badges = row[4] or ""
        return {
            "id": str(row[0]),
            "username": row[1],
            "email": row[2],
            "tier": row[3] or "free",
            "badges": badges.split(",") if badges else [],
            "created_at": row[5],
            "last_login": row[6]
        }


@router.put("/users/{user_id}/tier")
async def update_user_tier(
    user_id: str,
    update: UserTierUpdate,
    request: Request,
    db = Depends(get_db)
):
    """Update a user's tier."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # Check user exists
    row = db.fetch_one("SELECT id, username FROM auth_users WHERE id = ?", (uid,))
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    username = row["username"] if isinstance(row, dict) else row[1]

    # Update tier
    db.execute("UPDATE auth_users SET tier = ? WHERE id = ?", (update.tier, uid))

    logger.info(f"Admin {admin_id} updated user {username} ({uid}) tier to {update.tier}")

    return {"success": True, "user_id": user_id, "tier": update.tier}


@router.post("/users/{user_id}/badges/{badge}")
async def add_user_badge(
    user_id: str,
    badge: str,
    request: Request,
    db = Depends(get_db)
):
    """Add a badge to a user."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # Get current badges
    row = db.fetch_one("SELECT badges FROM auth_users WHERE id = ?", (uid,))
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    current_badges = (row["badges"] if isinstance(row, dict) else row[0]) or ""
    badges_list = [b for b in current_badges.split(",") if b]

    if badge not in badges_list:
        badges_list.append(badge)
        db.execute("UPDATE auth_users SET badges = ? WHERE id = ?", (",".join(badges_list), uid))
        logger.info(f"Admin {admin_id} added badge '{badge}' to user {uid}")

    return {"success": True, "badges": badges_list}


@router.delete("/users/{user_id}/badges/{badge}")
async def remove_user_badge(
    user_id: str,
    badge: str,
    request: Request,
    db = Depends(get_db)
):
    """Remove a badge from a user."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # Get current badges
    row = db.fetch_one("SELECT badges FROM auth_users WHERE id = ?", (uid,))
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    current_badges = (row["badges"] if isinstance(row, dict) else row[0]) or ""
    badges_list = [b for b in current_badges.split(",") if b]

    if badge in badges_list:
        badges_list.remove(badge)
        db.execute("UPDATE auth_users SET badges = ? WHERE id = ?", (",".join(badges_list), uid))
        logger.info(f"Admin {admin_id} removed badge '{badge}' from user {uid}")

    return {"success": True, "badges": badges_list}


@router.get("/tiers")
async def get_available_tiers(request: Request, db = Depends(get_db)):
    """Get list of available tiers."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    return {
        "tiers": [
            {"id": "free", "name": "Free", "description": "Basic access"},
            {"id": "alpha", "name": "Alpha", "description": "Alpha tester access"},
            {"id": "beta", "name": "Beta", "description": "Beta tester access"},
            {"id": "premium", "name": "Premium", "description": "Premium features"},
            {"id": "staff", "name": "Staff", "description": "Staff member"}
        ]
    }


@router.get("/badges")
async def get_available_badges(request: Request, db = Depends(get_db)):
    """Get list of available badges."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    badges_config = config.get("user_features", {}).get("available_badges", [])

    return {
        "badges": badges_config or [
            "alpha_tester", "early_supporter", "staff",
            "verified", "bug_hunter", "contributor", "moderator", "partner"
        ]
    }


# ==================== Admin UI HTML ====================

ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlexiChat Admin Login</title>
    <style>
        :root { --bg: #1a1a2e; --card: #16213e; --accent: #e94560; --text: #eaeaea; --border: #0f3460; --error: #ef4444; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-card { background: var(--card); border-radius: 12px; padding: 40px; width: 100%; max-width: 400px; border: 1px solid var(--border); }
        h1 { color: var(--accent); margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #888; margin-bottom: 24px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 6px; font-size: 14px; color: #aaa; }
        input { width: 100%; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font-size: 16px; }
        input:focus { outline: none; border-color: var(--accent); }
        button { width: 100%; padding: 12px; background: var(--accent); color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; margin-top: 8px; }
        button:hover { opacity: 0.9; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .error { color: var(--error); font-size: 14px; margin-top: 12px; display: none; }
        .otp-setup { display: none; text-align: center; }
        .otp-setup img { max-width: 200px; margin: 16px 0; background: white; padding: 8px; border-radius: 8px; }
        .otp-setup .secret { font-family: monospace; background: var(--bg); padding: 8px; border-radius: 4px; margin: 8px 0; word-break: break-all; }
        .warning { background: #fbbf24; color: #000; padding: 12px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="login-card">
        <div id="login-form">
            <h1>PlexiChat Admin</h1>
            <p class="subtitle">Sign in to access the admin panel</p>
            <form onsubmit="login(event)">
                <div class="form-group">
                    <label for="username">Username</label>
                    <input type="text" id="username" required autocomplete="username">
                </div>
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" required autocomplete="current-password">
                </div>
                <button type="submit" id="login-btn">Sign In</button>
            </form>
            <div class="error" id="login-error"></div>
        </div>
        <div id="otp-setup" class="otp-setup">
            <h1>Setup 2FA</h1>
            <p class="subtitle">Scan this QR code with your authenticator app</p>
            <div class="warning">2FA is required for admin access. This is a one-time setup.</div>
            <img id="qr-code" src="" alt="QR Code">
            <p>Or enter this secret manually:</p>
            <div class="secret" id="otp-secret"></div>
            <form onsubmit="verifyOTP(event, true)">
                <div class="form-group">
                    <label for="setup-code">Enter code from app</label>
                    <input type="text" id="setup-code" required maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code">
                </div>
                <button type="submit">Verify & Enable 2FA</button>
            </form>
            <div class="error" id="setup-error"></div>
        </div>
        <div id="otp-verify" class="otp-setup">
            <h1>Enter 2FA Code</h1>
            <p class="subtitle">Enter the code from your authenticator app</p>
            <form onsubmit="verifyOTP(event, false)">
                <div class="form-group">
                    <label for="verify-code">6-digit code</label>
                    <input type="text" id="verify-code" required maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code">
                </div>
                <button type="submit">Verify</button>
            </form>
            <div class="error" id="verify-error"></div>
        </div>
    </div>
    <script>
        let adminId = null;
        async function login(e) {
            e.preventDefault();
            const btn = document.getElementById('login-btn');
            const err = document.getElementById('login-error');
            btn.disabled = true; err.style.display = 'none';
            try {
                const res = await fetch('/api/v1/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Login failed');
                if (data.status === 'otp_setup_required') {
                    adminId = data.admin_id;
                    document.getElementById('login-form').style.display = 'none';
                    document.getElementById('otp-setup').style.display = 'block';
                    document.getElementById('qr-code').src = 'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=' + encodeURIComponent(data.otp_qr_uri);
                    document.getElementById('otp-secret').textContent = data.otp_secret;
                } else if (data.status === 'otp_required') {
                    adminId = data.admin_id;
                    document.getElementById('login-form').style.display = 'none';
                    document.getElementById('otp-verify').style.display = 'block';
                } else if (data.token) {
                    localStorage.setItem('plexichat-admin-token', data.token);
                    window.location.href = '/api/v1/admin/ui';
                }
            } catch (e) { err.textContent = e.message; err.style.display = 'block'; }
            btn.disabled = false;
        }
        async function verifyOTP(e, isSetup) {
            e.preventDefault();
            const code = document.getElementById(isSetup ? 'setup-code' : 'verify-code').value;
            const err = document.getElementById(isSetup ? 'setup-error' : 'verify-error');
            err.style.display = 'none';
            try {
                const res = await fetch('/api/v1/admin/verify-otp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ admin_id: adminId, code: code, is_setup: isSetup })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Verification failed');
                localStorage.setItem('plexichat-admin-token', data.token);
                window.location.href = '/api/v1/admin/ui';
            } catch (e) { err.textContent = e.message; err.style.display = 'block'; }
        }
        // Check if already logged in
        const token = localStorage.getItem('plexichat-admin-token');
        if (token) {
            fetch('/api/v1/admin/dashboard', { headers: { 'Authorization': 'Bearer ' + token } })
                .then(r => { if (r.ok) window.location.href = '/api/v1/admin/ui'; });
        }
    </script>
</body>
</html>
"""


ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlexiChat Admin Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --bg: #1a1a2e; --card: #16213e; --accent: #e94560; --text: #eaeaea; --border: #0f3460; --good: #4ade80; --warn: #fbbf24; --bad: #ef4444; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        .header { background: var(--card); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); }
        .header h1 { color: var(--accent); font-size: 20px; }
        .logout-btn { background: transparent; border: 1px solid var(--border); color: var(--text); padding: 8px 16px; border-radius: 4px; cursor: pointer; }
        .logout-btn:hover { border-color: var(--accent); }
        .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card); border-radius: 8px; padding: 20px; border: 1px solid var(--border); }
        .card h3 { font-size: 12px; color: #888; margin-bottom: 8px; text-transform: uppercase; }
        .card .value { font-size: 28px; font-weight: bold; color: var(--accent); }
        .card .value.good { color: var(--good); }
        .card .value.warn { color: var(--warn); }
        .card .value.bad { color: var(--bad); }
        .card .subtitle { font-size: 11px; color: #666; margin-top: 4px; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab { padding: 10px 20px; background: var(--card); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; color: var(--text); }
        .tab.active { background: var(--accent); border-color: var(--accent); }
        table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 8px; overflow: hidden; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: var(--border); font-weight: 600; font-size: 12px; text-transform: uppercase; }
        .status { padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
        .status.open { background: #fbbf24; color: #000; }
        .status.in_progress { background: #3b82f6; color: #fff; }
        .status.resolved { background: #4ade80; color: #000; }
        .status.closed { background: #6b7280; color: #fff; }
        #error { color: #ef4444; padding: 16px; background: rgba(239,68,68,0.1); border-radius: 8px; margin-bottom: 16px; display: none; }
        .loading { text-align: center; padding: 40px; color: #888; }
        .chart-container { background: var(--card); border-radius: 8px; padding: 20px; border: 1px solid var(--border); margin-bottom: 16px; }
        .chart-container h3 { font-size: 14px; color: #888; margin-bottom: 16px; text-transform: uppercase; }
        .chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
        .chart-row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
        @media (max-width: 1200px) { .chart-row-3 { grid-template-columns: 1fr 1fr; } }
        @media (max-width: 900px) { .chart-row, .chart-row-3 { grid-template-columns: 1fr; } }
        .latency-good { color: var(--good); }
        .latency-warn { color: var(--warn); }
        .latency-bad { color: var(--bad); }
        .endpoint-row { cursor: pointer; transition: background 0.2s; }
        .endpoint-row:hover { background: rgba(233,69,96,0.1); }
        .endpoint-row.selected { background: rgba(233,69,96,0.2); }
        .emoji-cell { font-family: "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif; }
        #history-chart-container { display: none; }
        .summary-row { display: flex; gap: 24px; margin-bottom: 16px; flex-wrap: wrap; }
        .summary-item { display: flex; align-items: center; gap: 8px; font-size: 14px; }
        .summary-dot { width: 12px; height: 12px; border-radius: 50%; }
        .filter-row { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
        .filter-row select, .filter-row input { padding: 8px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; color: var(--text); }
        .filter-row select:focus, .filter-row input:focus { outline: none; border-color: var(--accent); }
        .refresh-btn { padding: 8px 16px; background: var(--accent); border: none; border-radius: 4px; color: white; cursor: pointer; }
        .refresh-btn:hover { opacity: 0.9; }
    </style>
</head>
<body>
    <div class="header">
        <h1>PlexiChat Admin</h1>
        <button class="logout-btn" onclick="logout()">Logout</button>
    </div>
    <div class="container">
        <div id="error"></div>
        <div id="loading" class="loading">Verifying session...</div>
        <div id="content" style="display:none">
            <div class="cards" id="stats"></div>
            <div class="tabs">
                <button class="tab active" onclick="showTab('tickets', this)">Tickets</button>
                <button class="tab" onclick="showTab('telemetry', this)">Telemetry</button>
                <button class="tab" onclick="showTab('hash-reports', this)">Hash Reports</button>
                <button class="tab" onclick="showTab('user-tiers', this)">User Tiers</button>
            </div>
            <div id="tickets-tab">
                <table id="tickets-table">
                    <thead><tr><th>ID</th><th>User</th><th>Category</th><th>Status</th><th>Created</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="hash-reports-tab" style="display:none">
                <div class="filter-row">
                    <select id="hash-status-filter" onchange="loadHashReports()">
                        <option value="">All Status</option>
                        <option value="pending">Pending</option>
                        <option value="reviewed">Reviewed</option>
                        <option value="blocked">Blocked</option>
                        <option value="cleared">Cleared</option>
                    </select>
                    <button class="refresh-btn" onclick="loadHashReports()">Refresh</button>
                    <button class="refresh-btn" onclick="showBlockHashModal()" style="background:#ef4444;margin-left:auto">Block Hash</button>
                </div>
                <div class="cards" id="hash-report-stats"></div>
                <h3 style="margin:16px 0 8px;color:#888;font-size:14px">PENDING REPORTS</h3>
                <table id="hash-reports-table">
                    <thead><tr><th>Hash</th><th>Reporter</th><th>Reason</th><th>Status</th><th>Reported</th><th>Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
                <h3 style="margin:24px 0 8px;color:#888;font-size:14px">BLOCKED HASHES</h3>
                <table id="blocked-hashes-table">
                    <thead><tr><th>Hash</th><th>Reason</th><th>Blocked At</th><th>Auto</th><th>Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <!-- Block Hash Modal -->
            <div id="block-hash-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center">
                <div style="background:var(--card);border-radius:12px;padding:24px;width:100%;max-width:500px;border:1px solid var(--border)">
                    <h2 style="color:var(--accent);margin-bottom:16px">Block Hash</h2>
                    <div class="form-group" style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:#aaa">SHA-256 Hash</label>
                        <input type="text" id="block-hash-value" style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:monospace" placeholder="Enter 64-character hash">
                    </div>
                    <div class="form-group" style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:#aaa">Reason</label>
                        <input type="text" id="block-hash-reason" style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)" placeholder="Reason for blocking">
                    </div>
                    <div style="display:flex;gap:12px;justify-content:flex-end">
                        <button onclick="hideBlockHashModal()" style="padding:10px 20px;background:transparent;border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer">Cancel</button>
                        <button onclick="blockHashManually()" style="padding:10px 20px;background:var(--accent);border:none;border-radius:4px;color:white;cursor:pointer">Block Hash</button>
                    </div>
                </div>
            </div>
            <div id="telemetry-tab" style="display:none">
                <div class="filter-row">
                    <select id="time-range" onchange="loadTelemetryStats()">
                        <option value="1">Last 1 hour</option>
                        <option value="6">Last 6 hours</option>
                        <option value="24" selected>Last 24 hours</option>
                        <option value="72">Last 3 days</option>
                        <option value="168">Last 7 days</option>
                    </select>
                    <select id="telemetry-source" onchange="loadTelemetryStats()">
                        <option value="">All Sources</option>
                        <option value="server">Server-side Only</option>
                        <option value="client">Client-side Only</option>
                    </select>
                    <button class="refresh-btn" onclick="loadTelemetryStats()">Refresh</button>
                    <select id="export-format" style="margin-left:auto">
                        <option value="json">JSON</option>
                        <option value="csv">CSV</option>
                        <option value="html">HTML</option>
                        <option value="txt">Text</option>
                    </select>
                    <button class="refresh-btn" onclick="exportStats()" style="background:#3b82f6">Export</button>
                    <button class="refresh-btn" onclick="resetStats()" style="background:#ef4444">Reset All</button>
                </div>
                <div class="cards" id="telemetry-summary"></div>
                <div class="chart-row">
                    <div class="chart-container">
                        <h3>Latency Distribution (Avg vs P95)</h3>
                        <canvas id="latency-chart"></canvas>
                    </div>
                    <div class="chart-container">
                        <h3>Request Volume</h3>
                        <canvas id="count-chart"></canvas>
                    </div>
                </div>
                <div class="chart-row">
                    <div class="chart-container">
                        <h3>Error Rate by Endpoint</h3>
                        <canvas id="error-chart"></canvas>
                    </div>
                    <div class="chart-container">
                        <h3>Slowest Endpoints (P95)</h3>
                        <canvas id="slow-chart"></canvas>
                    </div>
                </div>
                <div class="chart-container" id="history-chart-container">
                    <h3>Latency History: <span id="history-endpoint"></span></h3>
                    <canvas id="history-chart"></canvas>
                </div>
                <table id="telemetry-table">
                    <thead><tr><th>Endpoint</th><th>Method</th><th>Count</th><th>Avg (ms)</th><th>P50 (ms)</th><th>P95 (ms)</th><th>P99 (ms)</th><th>Error %</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="user-tiers-tab" style="display:none">
                <div class="filter-row">
                    <input type="text" id="user-search" placeholder="Search by username or ID..." style="flex:1;max-width:400px" onkeyup="if(event.key==='Enter')searchUsers()">
                    <button class="refresh-btn" onclick="searchUsers()">Search</button>
                </div>
                <div id="user-search-results" style="margin-bottom:24px"></div>
                <div id="user-details" style="display:none">
                    <div class="card" style="margin-bottom:16px">
                        <h3>USER DETAILS</h3>
                        <div id="user-info" style="margin-top:12px"></div>
                    </div>
                    <div class="card" style="margin-bottom:16px">
                        <h3>CHANGE TIER</h3>
                        <div style="display:flex;gap:12px;margin-top:12px;flex-wrap:wrap">
                            <select id="tier-select" style="padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
                                <option value="free">Free</option>
                                <option value="alpha">Alpha</option>
                                <option value="beta">Beta</option>
                                <option value="premium">Premium</option>
                                <option value="staff">Staff</option>
                            </select>
                            <button class="refresh-btn" onclick="updateUserTier()">Update Tier</button>
                        </div>
                    </div>
                    <div class="card">
                        <h3>BADGES</h3>
                        <div id="user-badges" style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap"></div>
                        <div style="display:flex;gap:12px;margin-top:16px;flex-wrap:wrap">
                            <select id="badge-select" style="padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
                                <option value="alpha_tester">Alpha Tester</option>
                                <option value="early_supporter">Early Supporter</option>
                                <option value="staff">Staff</option>
                                <option value="verified">Verified</option>
                                <option value="bug_hunter">Bug Hunter</option>
                                <option value="contributor">Contributor</option>
                                <option value="moderator">Moderator</option>
                                <option value="partner">Partner</option>
                            </select>
                            <button class="refresh-btn" onclick="addBadge()" style="background:#4ade80">Add Badge</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        const token = localStorage.getItem('plexichat-admin-token');
        let latencyChart = null, countChart = null, historyChart = null, errorChart = null, slowChart = null;
        let telemetryData = [];
        
        if (!token) {
            window.location.replace('/api/v1/admin');
        }
        
        function formatEndpoint(endpoint) {
            // Decode URL-encoded emojis and normalize display
            try {
                let decoded = decodeURIComponent(endpoint);
                // Replace {id} placeholders with cleaner display
                decoded = decoded.replace(/\\{id\\}/g, ':id');
                // Replace {emoji} placeholder
                decoded = decoded.replace(/\\{emoji\\}/g, ':emoji');
                return decoded;
            } catch (e) {
                return endpoint;
            }
        }
        
        function shortEndpoint(endpoint) {
            // Shorten endpoint for chart labels
            let short = formatEndpoint(endpoint).replace('/api/v1', '');
            if (short.length > 25) short = short.substring(0, 22) + '...';
            return short;
        }
        
        function getLatencyClass(ms) {
            if (ms < 100) return 'latency-good';
            if (ms < 500) return 'latency-warn';
            return 'latency-bad';
        }
        
        function getLatencyColor(ms) {
            if (ms < 100) return '#4ade80';
            if (ms < 500) return '#fbbf24';
            return '#ef4444';
        }
        
        function getValueClass(ms) {
            if (ms < 100) return 'good';
            if (ms < 500) return 'warn';
            return 'bad';
        }
        
        async function loadTelemetryStats() {
            const hours = document.getElementById('time-range')?.value || 24;
            const source = document.getElementById('telemetry-source')?.value || '';
            try {
                let url = `/api/v1/admin/telemetry/stats?hours=${hours}`;
                if (source) url += `&source=${source}`;
                
                const res = await fetch(url, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) return;
                const data = await res.json();
                telemetryData = data.stats || [];
                renderTelemetrySummary();
                renderTelemetryTable();
                renderCharts();
            } catch (e) {
                console.error('Failed to load telemetry stats:', e);
            }
        }
        
        function renderTelemetrySummary() {
            const container = document.getElementById('telemetry-summary');
            if (!telemetryData.length) {
                container.innerHTML = '';
                return;
            }
            
            const totalRequests = telemetryData.reduce((sum, t) => sum + t.count, 0);
            const avgLatency = telemetryData.reduce((sum, t) => sum + t.avg_ms * t.count, 0) / totalRequests;
            const maxP95 = Math.max(...telemetryData.map(t => t.p95_ms));
            const avgErrorRate = telemetryData.reduce((sum, t) => sum + t.error_rate * t.count, 0) / totalRequests;
            const slowEndpoints = telemetryData.filter(t => t.avg_ms > 500).length;
            const errorEndpoints = telemetryData.filter(t => t.error_rate > 5).length;
            
            container.innerHTML = `
                <div class="card"><h3>Total Requests</h3><div class="value">${totalRequests.toLocaleString()}</div></div>
                <div class="card"><h3>Avg Latency</h3><div class="value ${getValueClass(avgLatency)}">${avgLatency.toFixed(0)}ms</div></div>
                <div class="card"><h3>Max P95</h3><div class="value ${getValueClass(maxP95)}">${maxP95.toFixed(0)}ms</div></div>
                <div class="card"><h3>Avg Error Rate</h3><div class="value ${avgErrorRate > 5 ? 'bad' : avgErrorRate > 1 ? 'warn' : 'good'}">${avgErrorRate.toFixed(1)}%</div></div>
                <div class="card"><h3>Slow Endpoints</h3><div class="value ${slowEndpoints > 0 ? 'bad' : 'good'}">${slowEndpoints}</div><div class="subtitle">&gt;500ms avg</div></div>
                <div class="card"><h3>Error Endpoints</h3><div class="value ${errorEndpoints > 0 ? 'bad' : 'good'}">${errorEndpoints}</div><div class="subtitle">&gt;5% errors</div></div>
            `;
        }
        
        function renderTelemetryTable() {
            const tbody = document.querySelector('#telemetry-table tbody');
            if (!telemetryData.length) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888">No telemetry data</td></tr>';
                return;
            }
            // Sort by avg latency descending for visibility of slow endpoints
            const sorted = [...telemetryData].sort((a, b) => b.avg_ms - a.avg_ms);
            tbody.innerHTML = sorted.map((t, i) => {
                const displayEndpoint = formatEndpoint(t.endpoint);
                const avgClass = getLatencyClass(t.avg_ms);
                const p95Class = getLatencyClass(t.p95_ms);
                const errorClass = t.error_rate > 10 ? 'latency-bad' : (t.error_rate > 1 ? 'latency-warn' : 'latency-good');
                return `<tr class="endpoint-row" onclick="showHistory('${encodeURIComponent(t.endpoint)}', '${t.method}', ${i})">
                    <td class="emoji-cell">${displayEndpoint}</td>
                    <td>${t.method}</td>
                    <td>${t.count.toLocaleString()}</td>
                    <td class="${avgClass}">${t.avg_ms.toFixed(1)}</td>
                    <td>${(t.p50_ms || 0).toFixed(1)}</td>
                    <td class="${p95Class}">${t.p95_ms.toFixed(1)}</td>
                    <td>${(t.p99_ms || 0).toFixed(1)}</td>
                    <td class="${errorClass}">${t.error_rate.toFixed(1)}%</td>
                </tr>`;
            }).join('');
        }
        
        function renderCharts() {
            if (!telemetryData.length) return;
            
            // Sort by request count for main charts
            const byCount = [...telemetryData].sort((a, b) => b.count - a.count).slice(0, 10);
            // Sort by P95 latency for slow endpoints chart
            const byP95 = [...telemetryData].sort((a, b) => b.p95_ms - a.p95_ms).slice(0, 8);
            // Sort by error rate for error chart
            const byError = [...telemetryData].filter(t => t.error_rate > 0).sort((a, b) => b.error_rate - a.error_rate).slice(0, 8);
            
            const labels = byCount.map(t => shortEndpoint(t.endpoint));
            const avgData = byCount.map(t => t.avg_ms);
            const p95Data = byCount.map(t => t.p95_ms);
            const countData = byCount.map(t => t.count);
            
            const chartOptions = {
                responsive: true,
                maintainAspectRatio: true,
                plugins: { legend: { labels: { color: '#eaeaea' } } },
                scales: {
                    x: { ticks: { color: '#888', maxRotation: 45 }, grid: { color: '#0f3460' } },
                    y: { ticks: { color: '#888' }, grid: { color: '#0f3460' } }
                }
            };
            
            const horizontalOptions = {
                ...chartOptions,
                indexAxis: 'y',
                scales: {
                    x: { ticks: { color: '#888' }, grid: { color: '#0f3460' } },
                    y: { ticks: { color: '#888', font: { size: 10 } }, grid: { color: '#0f3460' } }
                }
            };
            
            // Latency chart - Avg vs P95 comparison
            if (latencyChart) latencyChart.destroy();
            latencyChart = new Chart(document.getElementById('latency-chart'), {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        { label: 'Avg (ms)', data: avgData, backgroundColor: '#3b82f6', borderRadius: 4 },
                        { label: 'P95 (ms)', data: p95Data, backgroundColor: '#e94560', borderRadius: 4 }
                    ]
                },
                options: horizontalOptions
            });
            
            // Request count chart - Doughnut
            if (countChart) countChart.destroy();
            countChart = new Chart(document.getElementById('count-chart'), {
                type: 'doughnut',
                data: {
                    labels,
                    datasets: [{ 
                        data: countData, 
                        backgroundColor: ['#e94560','#3b82f6','#4ade80','#fbbf24','#8b5cf6','#06b6d4','#f97316','#ec4899','#84cc16','#6366f1']
                    }]
                },
                options: { 
                    responsive: true, 
                    plugins: { 
                        legend: { position: 'right', labels: { color: '#eaeaea', font: { size: 10 } } }
                    }
                }
            });
            
            // Error rate chart
            if (errorChart) errorChart.destroy();
            if (byError.length > 0) {
                errorChart = new Chart(document.getElementById('error-chart'), {
                    type: 'bar',
                    data: {
                        labels: byError.map(t => shortEndpoint(t.endpoint)),
                        datasets: [{
                            label: 'Error Rate %',
                            data: byError.map(t => t.error_rate),
                            backgroundColor: byError.map(t => t.error_rate > 10 ? '#ef4444' : t.error_rate > 5 ? '#fbbf24' : '#4ade80'),
                            borderRadius: 4
                        }]
                    },
                    options: horizontalOptions
                });
            } else {
                // No errors - show empty state
                const ctx = document.getElementById('error-chart').getContext('2d');
                ctx.fillStyle = '#888';
                ctx.font = '14px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('No errors recorded', ctx.canvas.width / 2, ctx.canvas.height / 2);
            }
            
            // Slowest endpoints chart (P95)
            if (slowChart) slowChart.destroy();
            slowChart = new Chart(document.getElementById('slow-chart'), {
                type: 'bar',
                data: {
                    labels: byP95.map(t => shortEndpoint(t.endpoint)),
                    datasets: [{
                        label: 'P95 Latency (ms)',
                        data: byP95.map(t => t.p95_ms),
                        backgroundColor: byP95.map(t => getLatencyColor(t.p95_ms)),
                        borderRadius: 4
                    }]
                },
                options: horizontalOptions
            });
        }
        
        async function showHistory(endpoint, method, rowIndex) {
            document.querySelectorAll('.endpoint-row').forEach((r, i) => r.classList.toggle('selected', i === rowIndex));
            document.getElementById('history-chart-container').style.display = 'block';
            // Decode the endpoint for display (it was encoded in the onclick)
            const decodedEndpoint = decodeURIComponent(endpoint);
            document.getElementById('history-endpoint').textContent = formatEndpoint(decodedEndpoint) + ' (' + method + ')';
            
            const hours = document.getElementById('time-range')?.value || 24;
            const bucketMinutes = hours <= 6 ? 5 : hours <= 24 ? 15 : 60;
            
            try {
                const res = await fetch(`/api/v1/admin/telemetry/history?endpoint=${endpoint}&method=${method}&hours=${hours}&bucket_minutes=${bucketMinutes}`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) return;
                const data = await res.json();
                const history = data.history || [];
                
                if (history.length === 0) {
                    document.getElementById('history-chart-container').innerHTML = '<h3>Latency History: ' + formatEndpoint(decodedEndpoint) + '</h3><p style="color:#888;text-align:center;padding:40px">No history data available</p><canvas id="history-chart"></canvas>';
                    return;
                }
                
                if (historyChart) historyChart.destroy();
                historyChart = new Chart(document.getElementById('history-chart'), {
                    type: 'line',
                    data: {
                        labels: history.map(h => {
                            const d = new Date(h.timestamp);
                            return hours <= 24 ? d.toLocaleTimeString() : d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        }),
                        datasets: [
                            { label: 'Avg (ms)', data: history.map(h => h.avg_response_time_ms), borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', tension: 0.3, fill: true },
                            { label: 'Max (ms)', data: history.map(h => h.max_response_time_ms), borderColor: '#e94560', tension: 0.3, fill: false, borderDash: [5,5] },
                            { label: 'Min (ms)', data: history.map(h => h.min_response_time_ms), borderColor: '#4ade80', tension: 0.3, fill: false, borderDash: [2,2] }
                        ]
                    },
                    options: {
                        responsive: true,
                        interaction: { intersect: false, mode: 'index' },
                        plugins: { 
                            legend: { labels: { color: '#eaeaea' } },
                            tooltip: {
                                callbacks: {
                                    afterBody: function(context) {
                                        const idx = context[0].dataIndex;
                                        return 'Requests: ' + history[idx].count;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: { ticks: { color: '#888', maxTicksLimit: 12, maxRotation: 45 }, grid: { color: '#0f3460' } },
                            y: { ticks: { color: '#888' }, grid: { color: '#0f3460' }, title: { display: true, text: 'Latency (ms)', color: '#888' } }
                        }
                    }
                });
            } catch (e) {
                console.error('Failed to load history:', e);
            }
        }
        
        async function verifyAndLoad() {
            try {
                const res = await fetch('/api/v1/admin/dashboard', { 
                    headers: { 'Authorization': 'Bearer ' + token },
                    method: 'GET'
                });
                
                if (res.status === 401) {
                    localStorage.removeItem('plexichat-admin-token');
                    window.location.replace('/api/v1/admin');
                    return;
                }
                
                if (!res.ok) throw new Error('Failed to load dashboard');
                
                document.getElementById('loading').style.display = 'none';
                document.getElementById('content').style.display = 'block';
                
                const data = await res.json();
                document.getElementById('stats').innerHTML = `
                    <div class="card"><h3>Open</h3><div class="value">${data.tickets.open}</div></div>
                    <div class="card"><h3>In Progress</h3><div class="value">${data.tickets.in_progress}</div></div>
                    <div class="card"><h3>Resolved</h3><div class="value">${data.tickets.resolved}</div></div>
                    <div class="card"><h3>Total</h3><div class="value">${data.tickets.total}</div></div>
                `;
                
                const ticketsRes = await fetch('/api/v1/admin/tickets?limit=20', { 
                    headers: { 'Authorization': 'Bearer ' + token } 
                });
                if (ticketsRes.status === 401) {
                    localStorage.removeItem('plexichat-admin-token');
                    window.location.replace('/api/v1/admin');
                    return;
                }
                const tickets = await ticketsRes.json();
                document.querySelector('#tickets-table tbody').innerHTML = tickets.map(t => 
                    `<tr><td>${t.id}</td><td>${t.username}</td><td>${t.category || '-'}</td><td><span class="status ${t.status}">${t.status}</span></td><td>${new Date(t.created_at).toLocaleString()}</td></tr>`
                ).join('') || '<tr><td colspan="5" style="text-align:center;color:#888">No tickets</td></tr>';
                
                // Load full telemetry stats for charts
                await loadTelemetryStats();
            } catch (e) { 
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').style.display = 'block'; 
                document.getElementById('error').textContent = 'Error loading data: ' + e.message; 
            }
        }
        
        function showTab(name, btn) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tickets-tab').style.display = name === 'tickets' ? 'block' : 'none';
            document.getElementById('telemetry-tab').style.display = name === 'telemetry' ? 'block' : 'none';
            document.getElementById('hash-reports-tab').style.display = name === 'hash-reports' ? 'block' : 'none';
            document.getElementById('user-tiers-tab').style.display = name === 'user-tiers' ? 'block' : 'none';
            if (name === 'telemetry' && !telemetryData.length) loadTelemetryStats();
            if (name === 'hash-reports') loadHashReports();
        }
        
        // ==================== Hash Reports Functions ====================
        let hashReportsData = [];
        let blockedHashesData = [];
        
        async function loadHashReports() {
            const statusFilter = document.getElementById('hash-status-filter')?.value || '';
            try {
                // Load report counts
                const countsRes = await fetch('/api/v1/admin/hash-reports/counts', {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (countsRes.ok) {
                    const counts = await countsRes.json();
                    document.getElementById('hash-report-stats').innerHTML = `
                        <div class="card"><h3>Pending</h3><div class="value ${counts.pending > 0 ? 'warn' : ''}">${counts.pending}</div></div>
                        <div class="card"><h3>Blocked</h3><div class="value">${counts.blocked}</div></div>
                        <div class="card"><h3>Cleared</h3><div class="value">${counts.cleared}</div></div>
                        <div class="card"><h3>Total Reports</h3><div class="value">${counts.total}</div></div>
                    `;
                }
                
                // Load reports
                let url = '/api/v1/admin/hash-reports?limit=50';
                if (statusFilter) url += `&status_filter=${statusFilter}`;
                const reportsRes = await fetch(url, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (reportsRes.ok) {
                    hashReportsData = await reportsRes.json();
                    renderHashReportsTable();
                }
                
                // Load blocked hashes
                const blockedRes = await fetch('/api/v1/admin/blocked-hashes?limit=50', {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (blockedRes.ok) {
                    blockedHashesData = await blockedRes.json();
                    renderBlockedHashesTable();
                }
            } catch (e) {
                console.error('Failed to load hash reports:', e);
            }
        }
        
        function renderHashReportsTable() {
            const tbody = document.querySelector('#hash-reports-table tbody');
            if (!hashReportsData.length) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888">No reports</td></tr>';
                return;
            }
            tbody.innerHTML = hashReportsData.map(r => {
                const shortHash = r.hash_value.substring(0, 16) + '...';
                const statusClass = r.status === 'pending' ? 'warn' : r.status === 'blocked' ? 'bad' : r.status === 'cleared' ? 'good' : '';
                const date = new Date(r.reported_at).toLocaleString();
                const actions = r.status === 'pending' ? `
                    <button onclick="reviewReport(${r.id}, 'block')" style="padding:4px 8px;background:#ef4444;border:none;border-radius:4px;color:white;cursor:pointer;margin-right:4px">Block</button>
                    <button onclick="reviewReport(${r.id}, 'clear')" style="padding:4px 8px;background:#4ade80;border:none;border-radius:4px;color:#000;cursor:pointer;margin-right:4px">Clear</button>
                    <button onclick="reviewReport(${r.id}, 'dismiss')" style="padding:4px 8px;background:#6b7280;border:none;border-radius:4px;color:white;cursor:pointer">Dismiss</button>
                ` : '<span style="color:#888">Reviewed</span>';
                return `<tr>
                    <td style="font-family:monospace;font-size:12px" title="${r.hash_value}">${shortHash}</td>
                    <td>${r.reporter_username || 'Unknown'}</td>
                    <td>${r.reason}</td>
                    <td><span class="status ${r.status}" style="background:${r.status === 'pending' ? '#fbbf24' : r.status === 'blocked' ? '#ef4444' : r.status === 'cleared' ? '#4ade80' : '#6b7280'};color:${r.status === 'pending' || r.status === 'cleared' ? '#000' : '#fff'}">${r.status}</span></td>
                    <td>${date}</td>
                    <td>${actions}</td>
                </tr>`;
            }).join('');
        }
        
        function renderBlockedHashesTable() {
            const tbody = document.querySelector('#blocked-hashes-table tbody');
            if (!blockedHashesData.length) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#888">No blocked hashes</td></tr>';
                return;
            }
            tbody.innerHTML = blockedHashesData.map(h => {
                const shortHash = h.hash_value.substring(0, 16) + '...';
                const date = new Date(h.blocked_at).toLocaleString();
                return `<tr>
                    <td style="font-family:monospace;font-size:12px" title="${h.hash_value}">${shortHash}</td>
                    <td>${h.reason}</td>
                    <td>${date}</td>
                    <td>${h.auto_blocked ? '<span style="color:#fbbf24">Auto</span>' : 'Manual'}</td>
                    <td><button onclick="unblockHash('${h.hash_value}')" style="padding:4px 8px;background:#3b82f6;border:none;border-radius:4px;color:white;cursor:pointer">Unblock</button></td>
                </tr>`;
            }).join('');
        }
        
        async function reviewReport(reportId, action) {
            const notes = action === 'block' ? prompt('Enter reason for blocking (optional):') : null;
            try {
                const res = await fetch(`/api/v1/admin/hash-reports/${reportId}/review`, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, notes })
                });
                if (!res.ok) throw new Error('Review failed');
                loadHashReports();
            } catch (e) {
                alert('Failed to review report: ' + e.message);
            }
        }
        
        async function unblockHash(hashValue) {
            if (!confirm('Are you sure you want to unblock this hash?')) return;
            try {
                const res = await fetch(`/api/v1/admin/blocked-hashes/${hashValue}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Unblock failed');
                loadHashReports();
            } catch (e) {
                alert('Failed to unblock hash: ' + e.message);
            }
        }
        
        function showBlockHashModal() {
            document.getElementById('block-hash-modal').style.display = 'flex';
        }
        
        function hideBlockHashModal() {
            document.getElementById('block-hash-modal').style.display = 'none';
            document.getElementById('block-hash-value').value = '';
            document.getElementById('block-hash-reason').value = '';
        }
        
        async function blockHashManually() {
            const hashValue = document.getElementById('block-hash-value').value.trim();
            const reason = document.getElementById('block-hash-reason').value.trim();
            if (!hashValue || hashValue.length < 64) {
                alert('Please enter a valid SHA-256 hash (64 characters)');
                return;
            }
            if (!reason) {
                alert('Please enter a reason');
                return;
            }
            try {
                const res = await fetch('/api/v1/admin/blocked-hashes', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hash_value: hashValue, reason })
                });
                if (!res.ok) throw new Error('Block failed');
                hideBlockHashModal();
                loadHashReports();
            } catch (e) {
                alert('Failed to block hash: ' + e.message);
            }
        }
        
        async function exportStats() {
            const hours = document.getElementById('time-range')?.value || 24;
            const format = document.getElementById('export-format')?.value || 'json';
            try {
                const res = await fetch(`/api/v1/admin/telemetry/export?format=${format}&hours=${hours}`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Export failed');
                
                if (format === 'json') {
                    const data = await res.json();
                    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `telemetry_${new Date().toISOString().slice(0,19).replace(/[T:]/g, '-')}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                } else {
                    const blob = await res.blob();
                    const disposition = res.headers.get('Content-Disposition');
                    let filename = `telemetry_export.${format}`;
                    if (disposition) {
                        const match = disposition.match(/filename=([^;]+)/);
                        if (match) filename = match[1];
                    }
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(url);
                }
            } catch (e) {
                alert('Export failed: ' + e.message);
            }
        }
        
        async function resetStats() {
            if (!confirm('Are you sure you want to reset ALL telemetry statistics? This cannot be undone.')) return;
            try {
                const res = await fetch('/api/v1/admin/telemetry/reset', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                const data = await res.json();
                if (data.success) {
                    alert(`Reset complete. Deleted ${data.deleted_count} records.`);
                    loadTelemetryStats();
                } else {
                    alert('Reset failed: ' + (data.message || 'Unknown error'));
                }
            } catch (e) {
                alert('Reset failed: ' + e.message);
            }
        }
        
        // ==================== User Tier Functions ====================
        let selectedUserId = null;
        let selectedUserData = null;
        
        async function searchUsers() {
            const query = document.getElementById('user-search').value.trim();
            if (!query || query.length < 2) {
                document.getElementById('user-search-results').innerHTML = '<p style="color:#888">Enter at least 2 characters to search</p>';
                return;
            }
            
            try {
                const res = await fetch(`/api/v1/admin/users/search?q=${encodeURIComponent(query)}&limit=20`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Search failed');
                const data = await res.json();
                
                if (!data.users || data.users.length === 0) {
                    document.getElementById('user-search-results').innerHTML = '<p style="color:#888">No users found</p>';
                    return;
                }
                
                let html = '<table><thead><tr><th>ID</th><th>Username</th><th>Tier</th><th>Badges</th><th>Actions</th></tr></thead><tbody>';
                for (const user of data.users) {
                    const badges = user.badges.length > 0 ? user.badges.join(', ') : '-';
                    html += `<tr>
                        <td style="font-family:monospace;font-size:12px">${user.id}</td>
                        <td>${escapeHtml(user.username)}</td>
                        <td><span class="status" style="background:var(--accent)">${user.tier}</span></td>
                        <td style="font-size:12px">${escapeHtml(badges)}</td>
                        <td><button class="refresh-btn" onclick="selectUser('${user.id}')" style="padding:4px 12px;font-size:12px">Edit</button></td>
                    </tr>`;
                }
                html += '</tbody></table>';
                document.getElementById('user-search-results').innerHTML = html;
            } catch (e) {
                document.getElementById('user-search-results').innerHTML = `<p style="color:var(--bad)">Error: ${e.message}</p>`;
            }
        }
        
        async function selectUser(userId) {
            selectedUserId = userId;
            try {
                const res = await fetch(`/api/v1/admin/users/${userId}`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Failed to load user');
                selectedUserData = await res.json();
                
                document.getElementById('user-info').innerHTML = `
                    <p><strong>ID:</strong> <span style="font-family:monospace">${selectedUserData.id}</span></p>
                    <p><strong>Username:</strong> ${escapeHtml(selectedUserData.username)}</p>
                    <p><strong>Email:</strong> ${selectedUserData.email || '-'}</p>
                    <p><strong>Current Tier:</strong> <span class="status" style="background:var(--accent)">${selectedUserData.tier}</span></p>
                    <p><strong>Created:</strong> ${new Date(selectedUserData.created_at).toLocaleString()}</p>
                `;
                
                document.getElementById('tier-select').value = selectedUserData.tier;
                renderUserBadges();
                document.getElementById('user-details').style.display = 'block';
            } catch (e) {
                alert('Error loading user: ' + e.message);
            }
        }
        
        function renderUserBadges() {
            const container = document.getElementById('user-badges');
            if (!selectedUserData.badges || selectedUserData.badges.length === 0) {
                container.innerHTML = '<span style="color:#888">No badges</span>';
                return;
            }
            container.innerHTML = selectedUserData.badges.map(badge => 
                `<span class="status" style="background:var(--good);color:#000;cursor:pointer" onclick="removeBadge('${badge}')" title="Click to remove">${badge} ×</span>`
            ).join('');
        }
        
        async function updateUserTier() {
            if (!selectedUserId) return;
            const tier = document.getElementById('tier-select').value;
            
            try {
                const res = await fetch(`/api/v1/admin/users/${selectedUserId}/tier`, {
                    method: 'PUT',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tier })
                });
                if (!res.ok) throw new Error('Failed to update tier');
                
                selectedUserData.tier = tier;
                document.getElementById('user-info').querySelector('.status').textContent = tier;
                alert('Tier updated successfully!');
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        async function addBadge() {
            if (!selectedUserId) return;
            const badge = document.getElementById('badge-select').value;
            
            if (selectedUserData.badges.includes(badge)) {
                alert('User already has this badge');
                return;
            }
            
            try {
                const res = await fetch(`/api/v1/admin/users/${selectedUserId}/badges/${badge}`, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Failed to add badge');
                const data = await res.json();
                
                selectedUserData.badges = data.badges;
                renderUserBadges();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        async function removeBadge(badge) {
            if (!selectedUserId) return;
            if (!confirm(`Remove badge "${badge}" from this user?`)) return;
            
            try {
                const res = await fetch(`/api/v1/admin/users/${selectedUserId}/badges/${badge}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Failed to remove badge');
                const data = await res.json();
                
                selectedUserData.badges = data.badges;
                renderUserBadges();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function logout() {
            fetch('/api/v1/admin/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token } });
            localStorage.removeItem('plexichat-admin-token');
            window.location.replace('/api/v1/admin');
        }
        
        verifyAndLoad();
    </script>
</body>
</html>
"""


@router.get("", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Serve the admin login page."""
    _check_host_restriction(request)
    return HTMLResponse(content=ADMIN_LOGIN_HTML)


@router.get("/ui", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    """Serve the admin dashboard page."""
    _check_host_restriction(request)
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)
