"""
Capabilities routes - expose per-feature availability state to clients.

Clients use this to render notices/banners about which artifacts features are
currently usable (e.g. editor, whiteboard, voice transcription/recording).
"""

from typing import Dict, Any

from fastapi import APIRouter, Depends

import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.artifacts.capabilities import (
    get_artifact_capabilities,
    capability_to_dict,
)

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])


@router.get(
    "",
    summary="Get artifact feature capabilities",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_capabilities(
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Return the availability state of every artifacts feature for the current
    user. Used by the client to show availability notices.
    """
    try:
        capabilities = get_artifact_capabilities()
        return {
            feature: capability_to_dict(info) for feature, info in capabilities.items()
        }
    except Exception as e:
        logger.error(f"Failed to compute capabilities: {e}", exc_info=True)
        raise
