"""
Reports API routes - Message and user reporting endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from typing import Optional, List

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo


router = APIRouter(prefix="/reports", tags=["Reports"])


# ==================== Request/Response Models ====================

class MessageReportRequest(BaseModel):
    """Report a message."""
    message_id: str = Field(..., description="ID of the message to report")
    channel_id: str = Field(..., description="Channel containing the message")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for report")
    category: str = Field("other", description="Report category")
    details: Optional[str] = Field(None, max_length=2000, description="Additional details")
    server_id: Optional[str] = Field(None, description="Server ID if applicable")


class UserReportRequest(BaseModel):
    """Report a user."""
    user_id: str = Field(..., description="ID of the user to report")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for report")
    category: str = Field("other", description="Report category")
    details: Optional[str] = Field(None, max_length=2000, description="Additional details")
    evidence_message_ids: Optional[List[str]] = Field(None, description="Message IDs as evidence")


class ReportResponse(BaseModel):
    """Response for report submission."""
    success: bool
    report_id: str
    message: str


# ==================== Message Reports ====================

@router.post("/messages", response_model=ReportResponse)
async def report_message(
    body: MessageReportRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Report a message for content moderation.
    
    Reports are reviewed by moderators/admins.
    """
    reports = api.get_reports()
    messaging = api.get_messaging()
    
    if not reports:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Reports module not available"}}
        )
    
    try:
        message_id = int(body.message_id)
        channel_id = int(body.channel_id)
        server_id = int(body.server_id) if body.server_id else None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid ID format"}}
        )
    
    # Get message content and author for the report
    reported_user_id = None
    message_content = None
    
    if messaging:
        try:
            msg = messaging.get_message(message_id)
            if msg:
                reported_user_id = msg.author_id
                message_content = msg.content[:500] if msg.content else None
        except Exception:
            pass
    
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
            message_content=message_content
        )
        
        logger.info(f"User {current_user.user_id} reported message {message_id}")
        
        return ReportResponse(
            success=True,
            report_id=str(report.id),
            message="Report submitted successfully"
        )
    except Exception as e:
        logger.error(f"Failed to submit message report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Failed to submit report"}}
        )


# ==================== User Reports ====================

@router.post("/users", response_model=ReportResponse)
async def report_user(
    body: UserReportRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Report a user for behavior issues.
    
    Reports are reviewed by moderators/admins.
    """
    reports = api.get_reports()
    
    if not reports:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Reports module not available"}}
        )
    
    try:
        reported_user_id = int(body.user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid user ID"}}
        )
    
    # Can't report yourself
    if reported_user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Cannot report yourself"}}
        )
    
    evidence_ids = None
    if body.evidence_message_ids:
        try:
            evidence_ids = [int(mid) for mid in body.evidence_message_ids]
        except ValueError:
            pass
    
    try:
        report = reports.report_user(
            reporter_id=current_user.user_id,
            reported_user_id=reported_user_id,
            reason=body.reason,
            category=body.category,
            details=body.details,
            evidence_message_ids=evidence_ids
        )
        
        logger.info(f"User {current_user.user_id} reported user {reported_user_id}")
        
        return ReportResponse(
            success=True,
            report_id=str(report.id),
            message="Report submitted successfully"
        )
    except Exception as e:
        logger.error(f"Failed to submit user report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Failed to submit report"}}
        )
