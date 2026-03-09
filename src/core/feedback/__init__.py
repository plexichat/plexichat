"""
Feedback module - Handles user feedback submission and management.
"""

import time
from typing import Optional, Any
from dataclasses import dataclass

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

_db: Any = None
_setup_complete = False


@dataclass
class FeedbackEntry:
    """Represents a feedback entry."""

    id: int
    user_id: int
    content: str
    category: Optional[str]
    rating: Optional[int]
    created_at: int
    status: str = "open"
    resolved_at: Optional[int] = None
    resolved_by: Optional[int] = None
    internal_notes: Optional[str] = None


def setup(db: Any) -> None:
    """Initialize the feedback module."""
    global _db, _setup_complete
    _db = db
    _setup_complete = True
    logger.info("Feedback module initialized")


def is_setup() -> bool:
    """Check if module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError(
            "Feedback module not initialized. Call feedback.setup(db) first."
        )
    return _db


def create_tables(db: Any) -> None:
    """Create feedback tables."""
    schema = """
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
    try:
        converted = (
            db.convert_schema(schema) if hasattr(db, "convert_schema") else schema
        )
        db.execute(converted)
    except Exception as e:
        logger.error(f"Failed to create feedback table: {e}")


def submit_feedback(
    user_id: int,
    content: str,
    category: Optional[str] = None,
    rating: Optional[int] = None,
) -> int:
    """
    Submit user feedback.

    Returns the generated feedback ID.
    """
    db = _get_db()
    feedback_id = generate_snowflake_id()
    now = int(time.time() * 1000)

    db.execute(
        """INSERT INTO feedback (id, user_id, content, category, rating, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (feedback_id, user_id, content, category, rating, now),
    )

    return feedback_id


def get_feedback_by_id(feedback_id: int) -> Optional[FeedbackEntry]:
    """Get feedback entry by ID."""
    db = _get_db()
    row = db.fetch_one("SELECT * FROM feedback WHERE id = ?", (feedback_id,))
    if not row:
        return None
    return FeedbackEntry(**dict(row))
