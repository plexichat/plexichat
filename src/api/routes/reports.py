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

router = APIRouter(prefix="/reports", tags=["Reports"])


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

        if messaging:
            try:
                msg = messaging.get_message(current_user.user_id, message_id)
                if msg:
                    reported_user_id = msg.author_id
                    message_content = msg.content[:500] if msg.content else None
            except Exception as e:
                logger.debug(
                    f"Failed to fetch message {message_id} details for report: {e}"
                )

        try:
            report = reports.report_message(
                reporter_id=current_user.user_id,
                message_id=message_id,
                channel_id=channel_id,
                reason=body.reason,
                category=body.category,
                details=body.details,
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

        evidence_ids = None
        if body.evidence_message_ids:
            try:
                evidence_ids = [int(mid) for mid in body.evidence_message_ids]
            except ValueError:
                logger.debug(
                    f"User {current_user.user_id} provided invalid evidence message IDs: {body.evidence_message_ids}"
                )
                pass

        try:
            report = reports.report_user(
                reporter_id=current_user.user_id,
                reported_user_id=reported_user_id,
                reason=body.reason,
                category=body.category,
                details=body.details,
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
