"""
Health check endpoint.
"""

from fastapi import APIRouter
from pydantic import BaseModel

import utils.version as version

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Check API health status.
    
    Returns the current health status and API version.
    """
    try:
        ver = version.current_string()
    except RuntimeError:
        ver = "unknown"
    return HealthResponse(status="healthy", version=ver)
