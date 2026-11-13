"""Router and shared error responses for the PlexiStash routes."""

from fastapi import APIRouter

from src.api.schemas.common import ErrorResponse

router = APIRouter(
    prefix="/suite/stash",
    tags=["PlexiStash"],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)
