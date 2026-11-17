"""
Feedback API routes.

Handles user feedback submission with rate limiting.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request

import utils.config as config
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.feedback import (
    FeedbackCreate,
    FeedbackResponse,
    FeedbackStatusResponse,
)
from src.api.schemas.common import ErrorResponse, SnowflakeID
from src.core import ratelimit


router = APIRouter(prefix="/feedback", tags=["Feedback"])

#: Feedback categories are a closed set at the HTTP boundary; the
#: schema also constrains the charset and length.
KNOWN_FEEDBACK_CATEGORIES = frozenset(
    {"bug", "feature", "idea", "suggestion", "question",
     "complaint", "support", "general", "other", "feedback"}
)
MAX_FEEDBACK_CONTENT_LENGTH = 2000

# Feedback is stored verbatim and later rendered in moderator/admin UIs and
# log lines. The request schema only bounds the *length*, which leaves two
# injection primitives open to a caller that constructs the model directly:
#
#   * C0 control bytes other than newline/tab (NUL, ESC, backspace, ...) can
#     terminate a log line or break out of a quoted UI attribute;
#   * Unicode bidi/invisible overrides (e.g. U+202E "right-to-left override")
#     reorder surrounding text with no visible glyph, letting a submitter
#     disguise a malicious sentence as a benign one in a moderation queue.
#
# The report and enhanced-report routes already reject control characters in
# their free text; apply the same discipline here (defence in depth) and also
# strip the invisible/bidi code points used to spoof rendered text. Ordinary
# line breaks and tabs stay allowed so multi-line feedback remains usable.
_FEEDBACK_ALLOWED_CONTROLS = frozenset("\n\t")
_FEEDBACK_INVISIBLE_CODEPOINTS = frozenset(
    {
        0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
        0x2066, 0x2067, 0x2068, 0x2069,
        0xFEFF,
    }
)


def _safe_feedback_content(content) -> str:
    """Return feedback text free of log-breaking and UI-spoofing characters.

    Raises ``ValueError`` on the first disallowed code point so the caller can
    map it to the endpoint's uniform 400 response.
    """
    if not isinstance(content, str):
        raise ValueError("feedback content must be a string")
    for ch in content:
        code = ord(ch)
        if (code < 0x20 or code == 0x7F) and ch not in _FEEDBACK_ALLOWED_CONTROLS:
            raise ValueError("feedback content contains control characters")
        if code in _FEEDBACK_INVISIBLE_CODEPOINTS:
            raise ValueError("feedback content contains invisible characters")
    return content


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
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> FeedbackResponse:
    """
    Submit feedback.

    Rate limited to prevent spam.
    """
    user_id = current_user.user_id

    # Defence in depth: the schema bounds content and category shape, but a
    # caller constructing the model directly must still obey the same limits.
    category = feedback_data.category
    if category is not None and category not in KNOWN_FEEDBACK_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid feedback category"}},
        )
    if len(feedback_data.content) > MAX_FEEDBACK_CONTENT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid feedback content"}},
        )
    try:
        _safe_feedback_content(feedback_data.content)
    except ValueError:
        logger.warning(f"User {user_id} submitted feedback with disallowed characters")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid feedback content"}},
        )

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
        rl_result = await ratelimit.acheck_rate_limit(
            user_id=user_id,
            route="POST /feedback",
            is_internal=getattr(request.state, "is_internal", False),
        )
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

        return FeedbackResponse(
            id=SnowflakeID(feedback_id), message="Thank you for your feedback!"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in submit_feedback for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
        bucket_info = await ratelimit.aget_bucket_info(bucket_key)

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
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
