"""
Admin API routes.

Provides admin-only endpoints for server management.
Host-restricted by default to localhost only.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import time

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_current_user, get_db


router = APIRouter(tags=["admin"])


class TicketStatusUpdate(BaseModel):
    """Update ticket status."""
    status: str = Field(..., pattern="^(open|in_progress|resolved|closed)$")


class InternalNoteCreate(BaseModel):
    """Create internal note."""
    content: str = Field(..., min_length=1, max_length=2000)


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


def _check_admin_access(request: Request, current_user) -> int:
    """Check if user has admin access. Returns user_id or raises HTTPException."""
    # Get admin config
    admin_config = config.get("admin_ui", {})
    
    if not admin_config.get("enabled", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    
    # Check host restriction
    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        allowed_hosts = host_restriction.get("allowed_hosts", ["127.0.0.1", "localhost"])
        client_ip = request.client.host if request.client else "unknown"
        
        from src.core import admin
        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Get user ID
    user_id = getattr(current_user, 'user_id', None) or getattr(current_user, 'id', None)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    
    # Check admin role if required
    if admin_config.get("require_admin_role", True):
        from src.core import admin
        if not admin.is_admin(user_id):
            logger.warning(f"Non-admin user {user_id} attempted admin access")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    return user_id


@router.get("/dashboard")
async def get_dashboard(
    request: Request,
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get admin dashboard data."""
    _check_admin_access(request, current_user)
    
    from src.core import admin
    
    # Get ticket counts
    ticket_counts = admin.get_ticket_counts()
    
    # Get telemetry stats if available
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
                for s in stats[:20]  # Top 20 endpoints
            ]
    except Exception:
        pass
    
    return {
        "tickets": ticket_counts,
        "telemetry": telemetry_stats
    }


@router.get("/tickets", response_model=List[TicketResponse])
async def get_tickets(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get feedback tickets."""
    _check_admin_access(request, current_user)
    
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
async def get_ticket(
    ticket_id: int,
    request: Request,
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get a single ticket."""
    _check_admin_access(request, current_user)
    
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
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Update ticket status."""
    admin_id = _check_admin_access(request, current_user)
    
    from src.core import admin
    
    if not admin.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    admin.update_ticket_status(ticket_id, update.status, admin_id)
    logger.info(f"Admin {admin_id} updated ticket {ticket_id} status to {update.status}")
    
    return {"success": True, "status": update.status}


@router.get("/tickets/{ticket_id}/notes", response_model=List[NoteResponse])
async def get_ticket_notes(
    ticket_id: int,
    request: Request,
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get internal notes for a ticket."""
    _check_admin_access(request, current_user)
    
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
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Add internal note to a ticket."""
    admin_id = _check_admin_access(request, current_user)
    
    from src.core import admin
    
    if not admin.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    new_note = admin.add_internal_note(ticket_id, admin_id, note.content)
    logger.info(f"Admin {admin_id} added note to ticket {ticket_id}")
    
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
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get telemetry statistics."""
    _check_admin_access(request, current_user)
    
    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            return {"stats": [], "message": "Telemetry not initialized"}
        
        stats = telemetry.get_endpoint_stats(hours=hours, endpoint_filter=endpoint)
        
        return {
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
    except ImportError:
        return {"stats": [], "message": "Telemetry module not available"}


@router.get("/telemetry/history")
async def get_telemetry_history(
    request: Request,
    endpoint: str,
    method: str = "GET",
    hours: int = 24,
    bucket_minutes: int = 5,
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get telemetry history for an endpoint."""
    _check_admin_access(request, current_user)
    
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


# Admin UI HTML page
ADMIN_UI_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlexiChat Admin</title>
    <style>
        :root { --bg: #1a1a2e; --card: #16213e; --accent: #e94560; --text: #eaeaea; --border: #0f3460; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        h1 { color: var(--accent); margin-bottom: 20px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: var(--card); border-radius: 8px; padding: 20px; border: 1px solid var(--border); }
        .card h3 { font-size: 14px; color: #888; margin-bottom: 8px; }
        .card .value { font-size: 32px; font-weight: bold; color: var(--accent); }
        table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 8px; overflow: hidden; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: var(--border); font-weight: 600; }
        .status { padding: 4px 8px; border-radius: 4px; font-size: 12px; }
        .status.open { background: #fbbf24; color: #000; }
        .status.resolved { background: #4ade80; color: #000; }
        .tabs { display: flex; gap: 10px; margin-bottom: 20px; }
        .tab { padding: 10px 20px; background: var(--card); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; }
        .tab.active { background: var(--accent); border-color: var(--accent); }
        #error { color: #ef4444; padding: 10px; display: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>PlexiChat Admin Dashboard</h1>
        <div id="error"></div>
        <div class="cards" id="stats"></div>
        <div class="tabs">
            <button class="tab active" onclick="showTab('tickets')">Tickets</button>
            <button class="tab" onclick="showTab('telemetry')">Telemetry</button>
        </div>
        <div id="tickets-tab"><table id="tickets-table"><thead><tr><th>ID</th><th>User</th><th>Category</th><th>Status</th><th>Created</th></tr></thead><tbody></tbody></table></div>
        <div id="telemetry-tab" style="display:none"><table id="telemetry-table"><thead><tr><th>Endpoint</th><th>Method</th><th>Count</th><th>Avg (ms)</th><th>P95 (ms)</th><th>Error %</th></tr></thead><tbody></tbody></table></div>
    </div>
    <script>
        const token = localStorage.getItem('plexichat-admin-token') || prompt('Enter admin token:');
        if (token) localStorage.setItem('plexichat-admin-token', token);
        const api = (path) => fetch('/api/v1/admin' + path, { headers: { 'Authorization': 'Bearer ' + token } }).then(r => r.json());
        async function load() {
            try {
                const data = await api('/dashboard');
                document.getElementById('stats').innerHTML = `
                    <div class="card"><h3>Open Tickets</h3><div class="value">${data.tickets.open}</div></div>
                    <div class="card"><h3>In Progress</h3><div class="value">${data.tickets.in_progress}</div></div>
                    <div class="card"><h3>Resolved</h3><div class="value">${data.tickets.resolved}</div></div>
                    <div class="card"><h3>Total</h3><div class="value">${data.tickets.total}</div></div>
                `;
                const tickets = await api('/tickets?limit=20');
                document.querySelector('#tickets-table tbody').innerHTML = tickets.map(t => `<tr><td>${t.id}</td><td>${t.username}</td><td>${t.category || '-'}</td><td><span class="status ${t.status}">${t.status}</span></td><td>${new Date(t.created_at).toLocaleString()}</td></tr>`).join('');
                document.querySelector('#telemetry-table tbody').innerHTML = data.telemetry.map(t => `<tr><td>${t.endpoint}</td><td>${t.method}</td><td>${t.count}</td><td>${t.avg_ms}</td><td>${t.p95_ms}</td><td>${t.error_rate}%</td></tr>`).join('');
            } catch (e) { document.getElementById('error').style.display = 'block'; document.getElementById('error').textContent = 'Error: ' + e.message; }
        }
        function showTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById('tickets-tab').style.display = name === 'tickets' ? 'block' : 'none';
            document.getElementById('telemetry-tab').style.display = name === 'telemetry' ? 'block' : 'none';
        }
        load();
    </script>
</body>
</html>
"""


@router.get("", response_class=HTMLResponse)
async def admin_ui(request: Request):
    """Serve the admin UI HTML page."""
    # Check host restriction only (no auth for initial page load)
    admin_config = config.get("admin_ui", {})
    
    if not admin_config.get("enabled", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    
    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        allowed_hosts = host_restriction.get("allowed_hosts", ["127.0.0.1", "localhost"])
        client_ip = request.client.host if request.client else "unknown"
        
        from src.core import admin
        if not admin.check_host_restriction(client_ip, allowed_hosts):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    return HTMLResponse(content=ADMIN_UI_HTML)
