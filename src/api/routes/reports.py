"""
Reports API routes - Message and user reporting endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, status

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.reports import (
    MessageReportRequest,
    UserReportRequest,
    ReportResponse,
)
from src.api.schemas.common import ErrorResponse
from src.core.reports import ReportCategory
from src.core.security.hardening import HardeningViolation, reject_control_characters

router = APIRouter(prefix="/reports", tags=["Reports"])

# Maximum reports a single user may submit per rolling minute.
REPORT_RATE_LIMIT = 10

# Report payloads are stored and later rendered in moderator UIs. Bound every
# caller-controlled field at the route boundary, not only in the request
# schema, so a direct manager call or a schema change cannot reintroduce an
# unbounded write.
MAX_REPORT_REASON_LENGTH = 500
MAX_REPORT_DETAILS_LENGTH = 2000
MAX_REPORT_MESSAGE_CONTENT = 500
MAX_EVIDENCE_IDS = 20
MAX_EVIDENCE_ID_LENGTH = 32
# Report listings live on the admin routes, but the same page contract belongs
# beside the submission route so every caller uses one bounded definition.
MAX_REPORT_PAGE_SIZE = 100


def _bounded_page_params(limit, offset):
    """Clamp pagination values; SQLite reads LIMIT -1 as *unlimited*."""
    try:
        page_limit = int(limit)
    except (TypeError, ValueError):
        page_limit = 0
    try:
        page_offset = int(offset)
    except (TypeError, ValueError):
        page_offset = 0
    return max(0, min(page_limit, MAX_REPORT_PAGE_SIZE)), max(0, page_offset)


def _bad_report_field(field: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": {
                "code": 400,
                "message": f"Invalid or oversized {field}",
            }
        },
    )


def _bounded_report_field(
    value, field: str, maximum: int, *, required: bool = False, truncate: bool = False
):
    """Validate (or truncate) a text field before it enters the report store."""
    if value is None:
        if required:
            raise _bad_report_field(field)
        return None
    if truncate and not isinstance(value, str):
        value = str(value)
    if not isinstance(value, str):
        raise _bad_report_field(field)
    try:
        text = reject_control_characters(value, field)
    except HardeningViolation:
        raise _bad_report_field(field) from None
    if len(text) > maximum:
        if truncate:
            return text[:maximum]
        raise _bad_report_field(field)
    return text


def _bounded_evidence_ids(values):
    """Cap the evidence list length and each element's text length."""
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or len(values) > MAX_EVIDENCE_IDS:
        raise _bad_report_field("evidence_message_ids")
    result = []
    for value in values:
        if not isinstance(value, str) or len(value) > MAX_EVIDENCE_ID_LENGTH:
            raise _bad_report_field("evidence_message_ids")
        result.append(value)
    return result


def _check_report_rate_limit(user_id: int) -> None:
    """Atomically throttle report submissions in the shared cache tier."""
    try:
        from src.core.database import check_rate_limit

        allowed, _remaining = check_rate_limit(
            f"reports:submit:{user_id}", REPORT_RATE_LIMIT, 60
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"Report rate-limit unavailable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": 503,
                    "message": "Report submission temporarily unavailable",
                }
            },
        )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many reports submitted. Please slow down.",
                }
            },
        )


# ==================== Message Reports ====================


@router.post(
    "/messages",
    response_model=ReportResponse,
    summary="Report a message",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID format"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def report_message(
    body: MessageReportRequest, current_user: TokenInfo = Depends(get_current_user)
) -> ReportResponse:
    """
    Report a message for content moderation.

    Reports are reviewed by moderators/admins.
    """
    reports = api.get_reports()
    messaging = api.get_messaging()

    if not reports:
        logger.error("Reports module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    _check_report_rate_limit(current_user.user_id)

    reason = _bounded_report_field(
        body.reason, "reason", MAX_REPORT_REASON_LENGTH, required=True
    )
    details = _bounded_report_field(body.details, "details", MAX_REPORT_DETAILS_LENGTH)

    # Category must be a recognised value (lowercase enum names).
    if body.category not in {c.value for c in ReportCategory}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid report category"}},
        )

    try:
        try:
            message_id = int(body.message_id)
            channel_id = int(body.channel_id)
            server_id = int(body.server_id) if body.server_id else None
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid ID format for message report: message_id={body.message_id}, channel_id={body.channel_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid ID format"}},
            )

        # Get message content and author for the report
        reported_user_id = None
        message_content = None

        # Fail closed when the messaging boundary is unavailable: without it
        # the reported message/author cannot be verified, so accepting the
        # report would store attacker-supplied IDs (F-AUTHZ-1).
        if not messaging:
            logger.error("Reports module cannot verify message: messaging unavailable")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": 503,
                        "message": "Report submission temporarily unavailable",
                    }
                },
            )

        if messaging:
            try:
                msg = messaging.get_message(current_user.user_id, message_id)
            except Exception as e:
                logger.debug(
                    f"Failed to fetch message {message_id} details for report: {e}"
                )
                msg = None
            # If the messaging boundary is wired we must be able to actually
            # see the message; otherwise a reporter could fabricate reports
            # against arbitrary message/channel ids they cannot access.
            if msg is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Message not found or not accessible",
                        }
                    },
                )
            reported_user_id = msg.author_id
            message_content = _bounded_report_field(
                msg.content,
                "message_content",
                MAX_REPORT_MESSAGE_CONTENT,
                truncate=True,
            )
            # A user may not report their own message.
            if reported_user_id == current_user.user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot report your own message",
                        }
                    },
                )

        # De-duplicate: an identical still-pending report is returned as a
        # success instead of creating a second queue entry.
        existing = reports.find_open_message_report(current_user.user_id, message_id)
        if existing is not None:
            return ReportResponse(
                success=True,
                report_id=str(existing.id),
                message="Report already submitted",
            )

        try:
            report = reports.report_message(
                reporter_id=current_user.user_id,
                message_id=message_id,
                channel_id=channel_id,
                reason=reason,
                category=body.category,
                details=details,
                server_id=server_id,
                reported_user_id=reported_user_id,
                message_content=message_content,
            )

            logger.info(
                f"User {current_user.user_id} reported message {message_id} (Report ID: {report.id})"
            )

            return ReportResponse(
                success=True,
                report_id=str(report.id),
                message="Report submitted successfully",
            )
        except Exception as e:
            logger.error(
                f"Failed to submit message report for message {message_id} by user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in report_message for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== User Reports ====================


@router.post(
    "/users",
    response_model=ReportResponse,
    summary="Report a user",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid user ID or reporting yourself",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def report_user(
    body: UserReportRequest, current_user: TokenInfo = Depends(get_current_user)
) -> ReportResponse:
    """
    Report a user for behavior issues.

    Reports are reviewed by moderators/admins.
    """
    reports = api.get_reports()

    if not reports:
        logger.error("Reports module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    _check_report_rate_limit(current_user.user_id)

    reason = _bounded_report_field(
        body.reason, "reason", MAX_REPORT_REASON_LENGTH, required=True
    )
    details = _bounded_report_field(body.details, "details", MAX_REPORT_DETAILS_LENGTH)
    evidence_values = _bounded_evidence_ids(body.evidence_message_ids)

    if body.category not in {c.value for c in ReportCategory}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid report category"}},
        )

    try:
        try:
            reported_user_id = int(body.user_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid user ID for user report: {body.user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        # Can't report yourself
        if reported_user_id == current_user.user_id:
            logger.warning(f"User {current_user.user_id} tried to report themselves")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Cannot report yourself"}},
            )

        # The reported user must actually exist (fail closed when the DB
        # is unavailable — otherwise arbitrary IDs are accepted).
        db = api.get_db()
        if db is None:
            logger.error("Reports module cannot verify user: database unavailable")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": 503,
                        "message": "Report submission temporarily unavailable",
                    }
                },
            )
        user_row = db.fetch_one(
            "SELECT id FROM auth_users WHERE id = ?", (reported_user_id,)
        )
        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Reported user not found"}},
            )

        evidence_ids = None
        if evidence_values:
            try:
                evidence_ids = [int(mid) for mid in evidence_values]
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Invalid evidence message ID",
                        }
                    },
                )

        # De-duplicate pending reports against the same user.
        existing = reports.find_open_user_report(current_user.user_id, reported_user_id)
        if existing is not None:
            return ReportResponse(
                success=True,
                report_id=str(existing.id),
                message="Report already submitted",
            )

        try:
            report = reports.report_user(
                reporter_id=current_user.user_id,
                reported_user_id=reported_user_id,
                reason=reason,
                category=body.category,
                details=details,
                evidence_message_ids=evidence_ids,
            )

            logger.info(
                f"User {current_user.user_id} reported user {reported_user_id} (Report ID: {report.id})"
            )

            return ReportResponse(
                success=True,
                report_id=str(report.id),
                message="Report submitted successfully",
            )
        except Exception as e:
            logger.error(
                f"Failed to submit user report for user {reported_user_id} by user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in report_user for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
