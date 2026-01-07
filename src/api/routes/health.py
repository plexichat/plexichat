"""
Health check endpoint.
"""

from fastapi import APIRouter, status, HTTPException
from pydantic import BaseModel

import utils.version as version
import utils.logger as logger
from src.api.schemas.common import ErrorResponse

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def health_check() -> HealthResponse:
    """
    Check API health status.

    Returns the current health status and API version.
    """
    try:
        try:
            ver = version.current_string()
        except RuntimeError:
            ver = "unknown"

        return HealthResponse(status="healthy", version=ver)
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
