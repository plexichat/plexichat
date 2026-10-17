"""Email verification routes.

POST /auth/verify-email       — consume an email-verification token.
POST /auth/resend-verification — re-send the verification email.

Both are public endpoints (whitelisted in the authentication middleware) and
rate limited via the route rate-limit config ("POST /auth/verify-email",
"POST /auth/resend-verification").
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import src.api as api
import utils.logger as logger
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Email Verification"])


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1, description="Email verification token")


class ResendVerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, description="Account email address")


@router.post(
    "/verify-email",
    response_model=SuccessResponse,
    summary="Verify an email address",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def verify_email(body: VerifyEmailRequest) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        verified = auth.verify_email(body.token.strip())
    except Exception as e:
        logger.error(f"Email verification failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    if not verified:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "Invalid or expired verification token",
                }
            },
        )

    return SuccessResponse(success=True, message="Email verified")


@router.post(
    "/resend-verification",
    response_model=SuccessResponse,
    summary="Resend the email verification message",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def resend_verification(body: ResendVerificationRequest) -> SuccessResponse:
    """Request a fresh verification email.

    Enumeration-safe: the response is identical whether or not the address
    exists (or is already verified); the manager simply no-ops in those
    cases. Delivery failures are logged server-side only.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        auth.resend_verification(body.email.strip())
    except Exception as e:
        # Never leak address state through the response; log for operators.
        logger.warning(f"Verification resend failed for a requested address: {e}")

    return SuccessResponse(
        success=True,
        message=(
            "If that address belongs to an unverified account, a "
            "verification email has been sent"
        ),
    )
