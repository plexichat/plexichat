"""
Admin module - Administrative functions for PlexiChat server.

This module provides:
- Admin user management (is_admin flag)
- Feedback/ticket viewing and management
- Telemetry dashboard data
- Host restriction for admin access

Usage:
    from src.core import admin
    admin.setup(db, auth_module)
    
    # Check if user is admin
    if admin.is_admin(user_id):
        tickets = admin.get_feedback_tickets()
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import time

_db = None
_auth = None
_setup_complete = False


@dataclass
class FeedbackTicket:
    """A feedback/support ticket."""
    id: int
    user_id: int
    username: str
    content: str
    category: Optional[str]
    rating: Optional[int]
    status: str  # 'open', 'in_progress', 'resolved', 'closed'
    created_at: int
    resolved_at: Optional[int]
    resolved_by: Optional[int]
    internal_notes: Optional[str]


@dataclass
class AdminNote:
    """An internal admin note on a ticket."""
    id: int
    ticket_id: int
    admin_id: int
    admin_username: str
    content: str
    created_at: int


def setup(db, auth_module=None) -> None:
    """Initialize the admin module."""
    global _db, _auth, _setup_complete
    
    _db = db
    _auth = auth_module
    _create_tables()
    _setup_complete = True


def _create_tables() -> None:
    """Create admin tables if they don't exist."""
    global _db
    
    # Add is_admin column to auth_users if not exists
    try:
        _db.execute("ALTER TABLE auth_users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists
    
    # Ensure feedback table exists first
    feedback_schema = """
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        category TEXT,
        rating INTEGER,
        created_at INTEGER NOT NULL,
        status TEXT DEFAULT 'open',
        resolved_at INTEGER,
        resolved_by INTEGER,
        internal_notes TEXT,
        FOREIGN KEY (user_id) REFERENCES auth_users(id)
    )
    """
    _db.execute(_db.convert_schema(feedback_schema) if hasattr(_db, 'convert_schema') else feedback_schema)
    
    # Create admin_notes table
    schema = """
    CREATE TABLE IF NOT EXISTS admin_notes (
        id INTEGER PRIMARY KEY,
        ticket_id INTEGER NOT NULL,
        admin_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES feedback(id),
        FOREIGN KEY (admin_id) REFERENCES auth_users(id)
    )
    """
    _db.execute(_db.convert_schema(schema) if hasattr(_db, 'convert_schema') else schema)

    # Add status columns to feedback table if not exists
    try:
        _db.execute("ALTER TABLE feedback ADD COLUMN status TEXT DEFAULT 'open'")
    except Exception:
        pass
    try:
        _db.execute("ALTER TABLE feedback ADD COLUMN resolved_at INTEGER")
    except Exception:
        pass
    try:
        _db.execute("ALTER TABLE feedback ADD COLUMN resolved_by INTEGER")
    except Exception:
        pass
    try:
        _db.execute("ALTER TABLE feedback ADD COLUMN internal_notes TEXT")
    except Exception:
        pass
    
    _db.execute("CREATE INDEX IF NOT EXISTS idx_admin_notes_ticket ON admin_notes(ticket_id)")


def is_setup() -> bool:
    """Check if admin module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError("Admin not initialized. Call admin.setup(db) first.")
    return _db


def is_admin(user_id: int) -> bool:
    """Check if a user has admin privileges."""
    db = _get_db()
    row = db.fetch_one("SELECT is_admin FROM auth_users WHERE id = ?", (user_id,))
    if row:
        val = row["is_admin"] if isinstance(row, dict) else row[0]
        return bool(val)
    return False


def set_admin(user_id: int, is_admin_flag: bool) -> bool:
    """Set or remove admin privileges for a user."""
    db = _get_db()
    db.execute(
        "UPDATE auth_users SET is_admin = ? WHERE id = ?",
        (1 if is_admin_flag else 0, user_id)
    )
    return True


def get_feedback_tickets(
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[FeedbackTicket]:
    """Get feedback tickets with optional status filter."""
    db = _get_db()
    
    if status_filter:
        rows = db.fetch_all(
            """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                      COALESCE(f.status, 'open') as status, f.created_at, 
                      f.resolved_at, f.resolved_by, f.internal_notes
               FROM feedback f
               LEFT JOIN auth_users u ON f.user_id = u.id
               WHERE COALESCE(f.status, 'open') = ?
               ORDER BY f.created_at DESC
               LIMIT ? OFFSET ?""",
            (status_filter, limit, offset)
        )
    else:
        rows = db.fetch_all(
            """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                      COALESCE(f.status, 'open') as status, f.created_at,
                      f.resolved_at, f.resolved_by, f.internal_notes
               FROM feedback f
               LEFT JOIN auth_users u ON f.user_id = u.id
               ORDER BY f.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )
    
    tickets = []
    for row in rows:
        if isinstance(row, dict):
            tickets.append(FeedbackTicket(
                id=row["id"],
                user_id=row["user_id"],
                username=row["username"] or "Unknown",
                content=row["content"],
                category=row["category"],
                rating=row["rating"],
                status=row["status"],
                created_at=row["created_at"],
                resolved_at=row["resolved_at"],
                resolved_by=row["resolved_by"],
                internal_notes=row["internal_notes"]
            ))
        else:
            tickets.append(FeedbackTicket(
                id=row[0], user_id=row[1], username=row[2] or "Unknown",
                content=row[3], category=row[4], rating=row[5],
                status=row[6], created_at=row[7], resolved_at=row[8],
                resolved_by=row[9], internal_notes=row[10]
            ))
    return tickets


def get_ticket(ticket_id: int) -> Optional[FeedbackTicket]:
    """Get a single feedback ticket by ID."""
    db = _get_db()
    row = db.fetch_one(
        """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                  COALESCE(f.status, 'open') as status, f.created_at,
                  f.resolved_at, f.resolved_by, f.internal_notes
           FROM feedback f
           LEFT JOIN auth_users u ON f.user_id = u.id
           WHERE f.id = ?""",
        (ticket_id,)
    )
    if not row:
        return None
    
    if isinstance(row, dict):
        return FeedbackTicket(
            id=row["id"], user_id=row["user_id"], username=row["username"] or "Unknown",
            content=row["content"], category=row["category"], rating=row["rating"],
            status=row["status"], created_at=row["created_at"], resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"], internal_notes=row["internal_notes"]
        )
    return FeedbackTicket(
        id=row[0], user_id=row[1], username=row[2] or "Unknown",
        content=row[3], category=row[4], rating=row[5], status=row[6],
        created_at=row[7], resolved_at=row[8], resolved_by=row[9], internal_notes=row[10]
    )


def update_ticket_status(ticket_id: int, status: str, admin_id: int) -> bool:
    """Update the status of a feedback ticket."""
    db = _get_db()
    
    valid_statuses = ['open', 'in_progress', 'resolved', 'closed']
    if status not in valid_statuses:
        return False
    
    resolved_at = int(time.time() * 1000) if status in ['resolved', 'closed'] else None
    resolved_by = admin_id if status in ['resolved', 'closed'] else None
    
    db.execute(
        """UPDATE feedback SET status = ?, resolved_at = ?, resolved_by = ?
           WHERE id = ?""",
        (status, resolved_at, resolved_by, ticket_id)
    )
    return True


def add_internal_note(ticket_id: int, admin_id: int, content: str) -> Optional[AdminNote]:
    """Add an internal note to a ticket."""
    db = _get_db()
    
    try:
        from src.utils.encryption import generate_snowflake_id
        note_id = generate_snowflake_id()
    except ImportError:
        note_id = int(time.time() * 1000000)
    
    now = int(time.time() * 1000)
    
    db.execute(
        """INSERT INTO admin_notes (id, ticket_id, admin_id, content, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (note_id, ticket_id, admin_id, content, now)
    )
    
    # Get admin username
    row = db.fetch_one("SELECT username FROM auth_users WHERE id = ?", (admin_id,))
    username = (row["username"] if isinstance(row, dict) else row[0]) if row else "Unknown"
    
    return AdminNote(
        id=note_id, ticket_id=ticket_id, admin_id=admin_id,
        admin_username=username, content=content, created_at=now
    )


def get_ticket_notes(ticket_id: int) -> List[AdminNote]:
    """Get all internal notes for a ticket."""
    db = _get_db()
    
    rows = db.fetch_all(
        """SELECT n.id, n.ticket_id, n.admin_id, u.username, n.content, n.created_at
           FROM admin_notes n
           LEFT JOIN auth_users u ON n.admin_id = u.id
           WHERE n.ticket_id = ?
           ORDER BY n.created_at ASC""",
        (ticket_id,)
    )
    
    notes = []
    for row in rows:
        if isinstance(row, dict):
            notes.append(AdminNote(
                id=row["id"], ticket_id=row["ticket_id"], admin_id=row["admin_id"],
                admin_username=row["username"] or "Unknown", content=row["content"],
                created_at=row["created_at"]
            ))
        else:
            notes.append(AdminNote(
                id=row[0], ticket_id=row[1], admin_id=row[2],
                admin_username=row[3] or "Unknown", content=row[4], created_at=row[5]
            ))
    return notes


def get_ticket_counts() -> Dict[str, int]:
    """Get counts of tickets by status."""
    db = _get_db()
    
    counts = {'open': 0, 'in_progress': 0, 'resolved': 0, 'closed': 0, 'total': 0}
    
    rows = db.fetch_all(
        """SELECT COALESCE(status, 'open') as status, COUNT(*) as count
           FROM feedback GROUP BY COALESCE(status, 'open')"""
    )
    
    for row in rows:
        status = row["status"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        if status in counts:
            counts[status] = count
        counts['total'] += count
    
    return counts


def check_host_restriction(client_ip: str, allowed_hosts: List[str]) -> bool:
    """Check if client IP is allowed to access admin UI."""
    if not allowed_hosts:
        return True
    
    # Normalize localhost variations
    localhost_variants = ['127.0.0.1', 'localhost', '::1']
    
    for allowed in allowed_hosts:
        if allowed in localhost_variants and client_ip in localhost_variants:
            return True
        if client_ip == allowed:
            return True
        # Support CIDR notation (basic)
        if '/' in allowed:
            # Simple prefix match for now
            prefix = allowed.split('/')[0].rsplit('.', 1)[0]
            if client_ip.startswith(prefix):
                return True
    
    return False


__all__ = [
    'setup', 'is_setup', 'is_admin', 'set_admin',
    'get_feedback_tickets', 'get_ticket', 'update_ticket_status',
    'add_internal_note', 'get_ticket_notes', 'get_ticket_counts',
    'check_host_restriction', 'FeedbackTicket', 'AdminNote',
]
