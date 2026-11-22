"""
Feedback module - Handles user feedback submission and management.
"""

import re
import time
from typing import Optional, Any
from dataclasses import dataclass

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id
from src.core.security.hardening.text import CONTROL_CHARACTERS

_db: Any = None
_setup_complete = False


# The HTTP route validates these fields, but ``submit_feedback`` is also a
# public core helper (see the module README and internal callers such as the
# self-test ticket bootstrap). Enforce the same shape at the storage boundary:
# feedback is rendered verbatim in moderator/admin UIs and log lines, so an
# unvalidated direct caller can otherwise persist log-forging control bytes or
# bidi/invisible text that spoofs what a moderator sees.
MAX_FEEDBACK_CONTENT_LENGTH = 2000
MAX_CATEGORY_LENGTH = 50
_CATEGORY_RE = re.compile(r"^[a-z0-9_-]+$")
MIN_RATING = 1
MAX_RATING = 5

# Ordinary newlines/tabs keep multi-line feedback usable; everything else in
# the C0/C1 + DEL range can terminate a log line or break out of a UI attribute.
_ALLOWED_CONTROLS = frozenset("\n\t")
_INVISIBLE_CODEPOINTS = frozenset(
    {
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner
        0x200D,  # zero-width joiner
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x202A,  # left-to-right embedding
        0x202B,  # right-to-left embedding
        0x202C,  # pop directional formatting
        0x202D,  # left-to-right override
        0x202E,  # right-to-left override
        0x2066,  # left-to-right isolate
        0x2067,  # right-to-left isolate
        0x2068,  # first strong isolate
        0x2069,  # pop directional isolate
        0xFEFF,  # zero-width no-break space / BOM
    }
)


def _validated_content(content: Any) -> str:
    """Return feedback text safe to store, or raise ``ValueError``."""
    if not isinstance(content, str):
        raise ValueError("feedback content must be a string")
    if not content or len(content) > MAX_FEEDBACK_CONTENT_LENGTH:
        raise ValueError("invalid feedback content length")
    for ch in content:
        if ch in _ALLOWED_CONTROLS:
            continue
        # Use the project-wide gate so the storage boundary rejects the same
        # C1 (NEL), line/paragraph separator and bidi/format code points as the
        # HTTP route and report path. The hand-listed set below covers the
        # zero-width characters the shared regex deliberately omits.
        if CONTROL_CHARACTERS.search(ch):
            raise ValueError("feedback content contains control characters")
        if ord(ch) in _INVISIBLE_CODEPOINTS:
            raise ValueError("feedback content contains invisible characters")
    return content


def _validated_category(category: Any) -> Optional[str]:
    """Return a safe category label, or raise ``ValueError``."""
    if category is None:
        return None
    if not isinstance(category, str):
        raise ValueError("feedback category must be a string")
    if not category or len(category) > MAX_CATEGORY_LENGTH:
        raise ValueError("invalid feedback category length")
    if not _CATEGORY_RE.fullmatch(category):
        raise ValueError("invalid feedback category")
    return category


def _validated_rating(rating: Any) -> Optional[int]:
    """Return a rating in the documented 1-5 range, or raise ``ValueError``."""
    if rating is None:
        return None
    # bool is an int subclass but is never a valid rating.
    if isinstance(rating, bool) or not isinstance(rating, int):
        raise ValueError("feedback rating must be an integer")
    if not MIN_RATING <= rating <= MAX_RATING:
        raise ValueError("feedback rating out of range")
    return rating


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
    internal_notes_encrypted: Optional[str] = None
    internal_notes_format: Optional[str] = "plain"


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
        internal_notes_encrypted TEXT,
        internal_notes_format TEXT DEFAULT 'plain',
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
    # Validate at the storage boundary, before any side effect: direct callers
    # must obey the same constraints the HTTP route enforces.
    content = _validated_content(content)
    category = _validated_category(category)
    rating = _validated_rating(rating)

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
