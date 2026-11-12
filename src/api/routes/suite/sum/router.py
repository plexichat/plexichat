"""Router and shared error responses for the PlexiSum routes."""

from fastapi import APIRouter

from src.api.schemas.common import ErrorResponse

router = APIRouter(
    prefix="/suite/sum",
    tags=["PlexiSum"],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)
