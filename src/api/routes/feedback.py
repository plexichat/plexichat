"""
Feedback API routes.

Handles user feedback submission with rate limiting.
"""

from fastapi import APIRouter, Depends, HTTPException, status

import utils.config as config
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.feedback import (
    FeedbackCreate,
    FeedbackResponse,
    FeedbackStatusResponse,
)
from src.api.schemas.common import ErrorResponse
from src.core import ratelimit


router = APIRouter(prefix="/feedback", tags=["Feedback"])


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
    feedback_data: FeedbackCreate, current_user: TokenInfo = Depends(get_current_user)
) -> FeedbackResponse:
    """
    Submit feedback.

    Rate limited to prevent spam.
    """
    user_id = current_user.user_id

    try:
        # Check if feedback is enabled
        if not config.get("feedback.enabled", True):
            logger.warning(
                f"User {user_id} attempted to submit feedback but it is disabled"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": 503,
                        "message": "Feedback submission is currently disabled",
                    }
                },
            )

        # Check rate limit
        rl_result = ratelimit.check_rate_limit(user_id=user_id, route="POST /feedback")
        if not rl_result.allowed:
            logger.warning(f"User {user_id} hit feedback rate limit")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": 429,
                        "message": "Too many feedback submissions. Please try again later.",
                    }
                },
            )

        # Import feedback module
        try:
            from src.core import feedback

            if not feedback.is_setup():
                logger.error("Feedback core module is not setup")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": {
                            "code": 503,
                            "message": "Feedback system not initialized",
                        }
                    },
                )
        except ImportError:
            logger.error("Feedback core module not found")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {"code": 503, "message": "Feedback module not available"}
                },
            )

        # Create feedback entry via core module
        try:
            feedback_id = feedback.submit_feedback(
                user_id=user_id,
                content=feedback_data.content,
                category=feedback_data.category,
                rating=feedback_data.rating,
            )
        except Exception as e:
            logger.error(
                f"Failed to submit feedback for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to submit feedback: {str(e)}",
                    }
                },
            )

        logger.info(f"Feedback {feedback_id} submitted by user {user_id}")

        return FeedbackResponse(id=feedback_id, message="Thank you for your feedback!")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in submit_feedback for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
async def get_feedback_status(
    current_user: TokenInfo = Depends(get_current_user),
) -> FeedbackStatusResponse:
    """Get feedback submission status for current user."""
    user_id = current_user.user_id

    try:
        from src.core.ratelimit.models import BucketType

        # Access the manager to get internal info
        manager = ratelimit.get_manager()
        bucket_key = manager._generate_bucket_key(
            BucketType.ROUTE, user_id=user_id, route="POST /feedback"
        )
        bucket_info = ratelimit.get_bucket_info(bucket_key)

        max_per_hour = config.get("feedback.rate_limit.max_per_hour", 5)
        max_per_day = config.get("feedback.rate_limit.max_per_day", 20)

        hour_count = bucket_info.hourly_count if bucket_info else 0
        day_count = bucket_info.daily_count if bucket_info else 0

        return FeedbackStatusResponse(
            enabled=config.get("feedback.enabled", True),
            submissions_this_hour=hour_count,
            submissions_today=day_count,
            max_per_hour=max_per_hour,
            max_per_day=max_per_day,
            can_submit=hour_count < max_per_hour and day_count < max_per_day,
        )
    except Exception as e:
        logger.error(
            f"Error getting feedback status for user {user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )
