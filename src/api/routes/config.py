"""
Configuration and capabilities routes - Expose public server constants.
"""

from fastapi import APIRouter, HTTPException, status
import utils.config as config
import utils.logger as logger
from src.api.schemas.config import ServerCapabilitiesResponse, AvatarConfigResponse
from src.api.schemas.common import ErrorResponse

router = APIRouter(tags=["System"])

@router.get(
    "/capabilities", 
    response_model=ServerCapabilitiesResponse, 
    summary="Get server capabilities",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_capabilities() -> ServerCapabilitiesResponse:
    """
    Get global server capabilities and configuration constants.
    
    This includes UI constants like avatar color palettes, size limits, 
    and supported features.
    """
    try:
        avatar_cfg = config.get("avatars", {})
        
        return ServerCapabilitiesResponse(
            avatars=AvatarConfigResponse(
                default_colors=avatar_cfg.get("default_colors", ["#e94560", "#4ade80", "#fbbf24", "#60a5fa", "#a78bfa", "#f472b6"]),
                max_size=avatar_cfg.get("max_size", 512),
                max_file_size=avatar_cfg.get("max_file_size", 5 * 1024 * 1024),
                allowed_types=avatar_cfg.get("allowed_types", ["image/jpeg", "image/png", "image/gif", "image/webp"])
            )
        )
    except Exception as e:
        logger.error(f"Failed to fetch server capabilities: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )
