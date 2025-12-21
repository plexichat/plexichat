"""
Feedback API routes.

Handles user feedback submission with rate limiting.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
import time

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_current_user, get_db
from src.utils.encryption import generate_snowflake_id


router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackCreate(BaseModel):
    """Feedback submission model."""
    content: str = Field(..., min_length=10, max_length=2000)
    category: Optional[str] = Field(None, max_length=50)
    rating: Optional[int] = Field(None, ge=1, le=5)


class FeedbackResponse(BaseModel):
    """Feedback response model."""
    id: int
    message: str


# In-memory rate limit tracking (per user)
_feedback_rate_limits = {}


def _check_rate_limit(user_id: int) -> bool:
    """Check if user is rate limited for feedback."""
    now = time.time()

    # Get config
    max_per_hour = config.get("feedback.rate_limit.max_per_hour", 5)
    max_per_day = config.get("feedback.rate_limit.max_per_day", 20)

    if user_id not in _feedback_rate_limits:
        _feedback_rate_limits[user_id] = []

    # Clean old entries
    hour_ago = now - 3600
    day_ago = now - 86400
    _feedback_rate_limits[user_id] = [
        t for t in _feedback_rate_limits[user_id] if t > day_ago
    ]

    # Check limits
    hour_count = sum(1 for t in _feedback_rate_limits[user_id] if t > hour_ago)
    day_count = len(_feedback_rate_limits[user_id])

    if hour_count >= max_per_hour:
        return False
    if day_count >= max_per_day:
        return False

    return True


def _record_feedback(user_id: int):
    """Record feedback submission for rate limiting."""
    now = time.time()
    if user_id not in _feedback_rate_limits:
        _feedback_rate_limits[user_id] = []
    _feedback_rate_limits[user_id].append(now)


def _ensure_feedback_table(db):
    """Ensure feedback table exists."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            category TEXT,
            rating INTEGER,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES auth_users(id)
        )
    """)


@router.post("", response_model=FeedbackResponse)
async def submit_feedback(
    feedback: FeedbackCreate,
    current_user = Depends(get_current_user),
    db = Depends(get_db)
):
    """
    Submit feedback.
    
    Rate limited to prevent spam.
    """
    # Get user ID from token info
    user_id = getattr(current_user, 'user_id', None) or getattr(current_user, 'id', None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user token"
        )

    # Check if feedback is enabled
    if not config.get("feedback.enabled", True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback submission is currently disabled"
        )

    # Check rate limit
    if not _check_rate_limit(user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many feedback submissions. Please try again later."
        )

    # Ensure table exists
    _ensure_feedback_table(db)

    # Create feedback entry
    feedback_id = generate_snowflake_id()
    now = int(time.time() * 1000)

    db.execute(
        """INSERT INTO feedback (id, user_id, content, category, rating, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (feedback_id, user_id, feedback.content, feedback.category, feedback.rating, now)
    )

    # Record for rate limiting
    _record_feedback(user_id)

    logger.info(f"Feedback submitted by user {user_id}")

    return FeedbackResponse(
        id=feedback_id,
        message="Thank you for your feedback!"
    )


@router.get("/status")
async def get_feedback_status(current_user = Depends(get_current_user)):
    """Get feedback submission status for current user."""
    now = time.time()
    hour_ago = now - 3600
    day_ago = now - 86400

    max_per_hour = config.get("feedback.rate_limit.max_per_hour", 5)
    max_per_day = config.get("feedback.rate_limit.max_per_day", 20)

    # Get user ID from token info
    user_id = getattr(current_user, 'user_id', None) or getattr(current_user, 'id', None)
    if not user_id:
        return {
            "enabled": config.get("feedback.enabled", True),
            "submissions_this_hour": 0,
            "submissions_today": 0,
            "max_per_hour": max_per_hour,
            "max_per_day": max_per_day,
            "can_submit": True
        }

    user_submissions = _feedback_rate_limits.get(user_id, [])
    user_submissions = [t for t in user_submissions if t > day_ago]

    hour_count = sum(1 for t in user_submissions if t > hour_ago)
    day_count = len(user_submissions)

    return {
        "enabled": config.get("feedback.enabled", True),
        "submissions_this_hour": hour_count,
        "submissions_today": day_count,
        "max_per_hour": max_per_hour,
        "max_per_day": max_per_day,
        "can_submit": hour_count < max_per_hour and day_count < max_per_day
    }
