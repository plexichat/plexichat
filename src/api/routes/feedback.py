"""
Feedback API routes.

Handles user feedback submission with rate limiting.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
import time

import utils.config as config
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.feedback import FeedbackCreate, FeedbackResponse, FeedbackStatusResponse
from src.api.schemas.common import ErrorResponse, SuccessResponse


router = APIRouter(prefix="/feedback", tags=["Feedback"])


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


@router.post(
    "",
    response_model=FeedbackResponse,
    summary="Submit feedback",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input data"},
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        429: {"model": ErrorResponse, "description": "Too many feedback submissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Feedback system unavailable"},
    },
)
async def submit_feedback(
    feedback_data: FeedbackCreate,
    current_user: TokenInfo = Depends(get_current_user)
) -> FeedbackResponse:
    """
    Submit feedback.
    
    Rate limited to prevent spam.
    """
    user_id = current_user.user_id

    try:
        # Check if feedback is enabled
        if not config.get("feedback.enabled", True):
            logger.warning(f"User {user_id} attempted to submit feedback but it is disabled")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"code": 503, "message": "Feedback submission is currently disabled"}}
            )

        # Check rate limit
        if not _check_rate_limit(user_id):
            logger.warning(f"User {user_id} hit feedback rate limit")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": {"code": 429, "message": "Too many feedback submissions. Please try again later."}}
            )

        # Import feedback module
        try:
            from src.core import feedback
            if not feedback.is_setup():
                logger.error("Feedback core module is not setup")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"error": {"code": 503, "message": "Feedback system not initialized"}}
                )
        except ImportError:
            logger.error("Feedback core module not found")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"code": 503, "message": "Feedback module not available"}}
            )

        # Create feedback entry via core module
        try:
            feedback_id = feedback.submit_feedback(
                user_id=user_id,
                content=feedback_data.content,
                category=feedback_data.category,
                rating=feedback_data.rating
            )
        except Exception as e:
            logger.error(f"Failed to submit feedback for user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": f"Failed to submit feedback: {str(e)}"}}
            )

        # Record for rate limiting
        _record_feedback(user_id)

        logger.info(f"Feedback {feedback_id} submitted by user {user_id}")

        return FeedbackResponse(
            id=feedback_id,
            message="Thank you for your feedback!"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in submit_feedback for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/status",
    response_model=FeedbackStatusResponse,
    summary="Get feedback status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_feedback_status(current_user: TokenInfo = Depends(get_current_user)) -> FeedbackStatusResponse:
    """Get feedback submission status for current user."""
    user_id = current_user.user_id
    
    try:
        now = time.time()
        hour_ago = now - 3600
        day_ago = now - 86400

        max_per_hour = config.get("feedback.rate_limit.max_per_hour", 5)
        max_per_day = config.get("feedback.rate_limit.max_per_day", 20)

        user_submissions = _feedback_rate_limits.get(user_id, [])
        user_submissions = [t for t in user_submissions if t > day_ago]

        hour_count = sum(1 for t in user_submissions if t > hour_ago)
        day_count = len(user_submissions)

        return FeedbackStatusResponse(
            enabled=config.get("feedback.enabled", True),
            submissions_this_hour=hour_count,
            submissions_today=day_count,
            max_per_hour=max_per_hour,
            max_per_day=max_per_day,
            can_submit=hour_count < max_per_hour and day_count < max_per_day
        )
    except Exception as e:
        logger.error(f"Error getting feedback status for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )
