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
from fastapi.responses import HTMLResponse, JSONResponse
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
    admin_id = _get_admin_from_token(request)
    
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
    admin_id = _get_admin_from_token(request)
    
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
    admin_id = _get_admin_from_token(request)
    
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
    admin_id = _get_admin_from_token(request)
    
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
    db = Depends(get_db)
):
    """Get telemetry statistics."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)
    
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
    db = Depends(get_db)
):
    """Get telemetry history for an endpoint."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)
    
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
                    window.location.href = '/api/v1/admin/dashboard';
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
                window.location.href = '/api/v1/admin/dashboard';
            } catch (e) { err.textContent = e.message; err.style.display = 'block'; }
        }
        // Check if already logged in
        const token = localStorage.getItem('plexichat-admin-token');
        if (token) {
            fetch('/api/v1/admin/dashboard', { headers: { 'Authorization': 'Bearer ' + token } })
                .then(r => { if (r.ok) window.location.href = '/api/v1/admin/dashboard'; });
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
    <style>
        :root { --bg: #1a1a2e; --card: #16213e; --accent: #e94560; --text: #eaeaea; --border: #0f3460; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        .header { background: var(--card); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); }
        .header h1 { color: var(--accent); font-size: 20px; }
        .logout-btn { background: transparent; border: 1px solid var(--border); color: var(--text); padding: 8px 16px; border-radius: 4px; cursor: pointer; }
        .logout-btn:hover { border-color: var(--accent); }
        .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card); border-radius: 8px; padding: 20px; border: 1px solid var(--border); }
        .card h3 { font-size: 12px; color: #888; margin-bottom: 8px; text-transform: uppercase; }
        .card .value { font-size: 28px; font-weight: bold; color: var(--accent); }
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
    </style>
</head>
<body>
    <div class="header">
        <h1>PlexiChat Admin</h1>
        <button class="logout-btn" onclick="logout()">Logout</button>
    </div>
    <div class="container">
        <div id="error"></div>
        <div class="cards" id="stats"></div>
        <div class="tabs">
            <button class="tab active" onclick="showTab('tickets', this)">Tickets</button>
            <button class="tab" onclick="showTab('telemetry', this)">Telemetry</button>
        </div>
        <div id="tickets-tab">
            <table id="tickets-table">
                <thead><tr><th>ID</th><th>User</th><th>Category</th><th>Status</th><th>Created</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>
        <div id="telemetry-tab" style="display:none">
            <table id="telemetry-table">
                <thead><tr><th>Endpoint</th><th>Method</th><th>Count</th><th>Avg (ms)</th><th>P95 (ms)</th><th>Error %</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
    <script>
        const token = localStorage.getItem('plexichat-admin-token');
        if (!token) window.location.href = '/api/v1/admin';
        const api = (path) => fetch('/api/v1/admin' + path, { headers: { 'Authorization': 'Bearer ' + token } })
            .then(r => { if (r.status === 401) { localStorage.removeItem('plexichat-admin-token'); window.location.href = '/api/v1/admin'; } return r.json(); });
        async function load() {
            try {
                const data = await api('/dashboard');
                document.getElementById('stats').innerHTML = `
                    <div class="card"><h3>Open</h3><div class="value">${data.tickets.open}</div></div>
                    <div class="card"><h3>In Progress</h3><div class="value">${data.tickets.in_progress}</div></div>
                    <div class="card"><h3>Resolved</h3><div class="value">${data.tickets.resolved}</div></div>
                    <div class="card"><h3>Total</h3><div class="value">${data.tickets.total}</div></div>
                `;
                const tickets = await api('/tickets?limit=20');
                document.querySelector('#tickets-table tbody').innerHTML = tickets.map(t => 
                    `<tr><td>${t.id}</td><td>${t.username}</td><td>${t.category || '-'}</td><td><span class="status ${t.status}">${t.status}</span></td><td>${new Date(t.created_at).toLocaleString()}</td></tr>`
                ).join('') || '<tr><td colspan="5" style="text-align:center;color:#888">No tickets</td></tr>';
                document.querySelector('#telemetry-table tbody').innerHTML = data.telemetry.map(t => 
                    `<tr><td>${t.endpoint}</td><td>${t.method}</td><td>${t.count}</td><td>${t.avg_ms}</td><td>${t.p95_ms}</td><td>${t.error_rate}%</td></tr>`
                ).join('') || '<tr><td colspan="6" style="text-align:center;color:#888">No telemetry data</td></tr>';
            } catch (e) { 
                document.getElementById('error').style.display = 'block'; 
                document.getElementById('error').textContent = 'Error loading data: ' + e.message; 
            }
        }
        function showTab(name, btn) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tickets-tab').style.display = name === 'tickets' ? 'block' : 'none';
            document.getElementById('telemetry-tab').style.display = name === 'telemetry' ? 'block' : 'none';
        }
        function logout() {
            fetch('/api/v1/admin/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token } });
            localStorage.removeItem('plexichat-admin-token');
            window.location.href = '/api/v1/admin';
        }
        load();
    </script>
</body>
</html>
"""


@router.get("", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Serve the admin login page."""
    _check_host_restriction(request)
    return HTMLResponse(content=ADMIN_LOGIN_HTML)


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    """Serve the admin dashboard page."""
    _check_host_restriction(request)
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)
