"""
Feedback and ticket management for PlexiChat Admin.
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import time

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

def get_feedback_tickets(db: Any, status_filter: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[FeedbackTicket]:
    """Get feedback tickets with optional status filter."""
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
            (status_filter, limit, offset),
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
            (limit, offset),
        )

    tickets = []
    for row in rows:
        if isinstance(row, dict):
            tickets.append(
                FeedbackTicket(
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
                    internal_notes=row["internal_notes"],
                )
            )
        else:
            tickets.append(
                FeedbackTicket(
                    id=row[0],
                    user_id=row[1],
                    username=row[2] or "Unknown",
                    content=row[3],
                    category=row[4],
                    rating=row[5],
                    status=row[6],
                    created_at=row[7],
                    resolved_at=row[8],
                    resolved_by=row[9],
                    internal_notes=row[10],
                )
            )
    return tickets

def get_ticket(db: Any, ticket_id: int) -> Optional[FeedbackTicket]:
    """Get a single feedback ticket by ID."""
    row = db.fetch_one(
        """SELECT f.id, f.user_id, u.username, f.content, f.category, f.rating,
                  COALESCE(f.status, 'open') as status, f.created_at,
                  f.resolved_at, f.resolved_by, f.internal_notes
           FROM feedback f
           LEFT JOIN auth_users u ON f.user_id = u.id
           WHERE f.id = ?""",
        (ticket_id,),
    )
    if not row:
        return None

    if isinstance(row, dict):
        return FeedbackTicket(
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
            internal_notes=row["internal_notes"],
        )
    return FeedbackTicket(
        id=row[0],
        user_id=row[1],
        username=row[2] or "Unknown",
        content=row[3],
        category=row[4],
        rating=row[5],
        status=row[6],
        created_at=row[7],
        resolved_at=row[8],
        resolved_by=row[9],
        internal_notes=row[10],
    )

def update_ticket_status(db: Any, ticket_id: int, status: str, admin_id: int) -> bool:
    """Update the status of a feedback ticket."""
    valid_statuses = ["open", "in_progress", "resolved", "closed"]
    if status not in valid_statuses:
        return False

    resolved_at = int(time.time() * 1000) if status in ["resolved", "closed"] else None
    resolved_by = admin_id if status in ["resolved", "closed"] else None

    db.execute(
        """UPDATE feedback SET status = ?, resolved_at = ?, resolved_by = ?
           WHERE id = ?""",
        (status, resolved_at, resolved_by, ticket_id),
    )
    return True

def add_internal_note(db: Any, ticket_id: int, admin_id: int, content: str) -> Optional[AdminNote]:
    """Add an internal note to a ticket."""
    try:
        from src.utils.encryption import generate_snowflake_id
        note_id = generate_snowflake_id()
    except ImportError:
        note_id = int(time.time() * 1000000)

    now = int(time.time() * 1000)

    db.execute(
        """INSERT INTO admin_notes (id, ticket_id, admin_id, content, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (note_id, ticket_id, admin_id, content, now),
    )

    # Get admin username
    row = db.fetch_one("SELECT username FROM admin_users WHERE id = ?", (admin_id,))
    username = (
        (row["username"] if isinstance(row, dict) else row[0]) if row else "Unknown"
    )

    return AdminNote(
        id=note_id,
        ticket_id=ticket_id,
        admin_id=admin_id,
        admin_username=username,
        content=content,
        created_at=now,
    )

def get_ticket_notes(db: Any, ticket_id: int) -> List[AdminNote]:
    """Get all internal notes for a ticket."""
    rows = db.fetch_all(
        """SELECT n.id, n.ticket_id, n.admin_id, u.username, n.content, n.created_at
           FROM admin_notes n
           LEFT JOIN admin_users u ON n.admin_id = u.id
           WHERE n.ticket_id = ?
           ORDER BY n.created_at ASC""",
        (ticket_id,),
    )

    notes = []
    for row in rows:
        if isinstance(row, dict):
            notes.append(
                AdminNote(
                    id=row["id"],
                    ticket_id=row["ticket_id"],
                    admin_id=row["admin_id"],
                    admin_username=row["username"] or "Unknown",
                    content=row["content"],
                    created_at=row["created_at"],
                )
            )
        else:
            notes.append(
                AdminNote(
                    id=row[0],
                    ticket_id=row[1],
                    admin_id=row[2],
                    admin_username=row[3] or "Unknown",
                    content=row[4],
                    created_at=row[5],
                )
            )
    return notes

def get_ticket_counts(db: Any) -> Dict[str, int]:
    """Get counts of tickets by status."""
    counts = {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0, "total": 0}

    rows = db.fetch_all(
        """SELECT COALESCE(status, 'open') as status, COUNT(*) as count
           FROM feedback GROUP BY COALESCE(status, 'open')"""
    )

    for row in rows:
        status = row["status"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        if status in counts:
            counts[status] = count
        counts["total"] += count

    return counts
