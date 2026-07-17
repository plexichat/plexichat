"""
Admin capabilities routes - expose per-feature availability state to admins.

Adds a top-level summary (counts of available vs. unavailable features) on top
of the per-feature capability breakdown used by the client endpoints.
"""

from typing import Any, Dict

from fastapi import APIRouter, Request

import utils.logger as logger
from src.core.artifacts.capabilities import (
    CapabilityState,
    get_artifact_capabilities,
    capability_to_dict,
)
from .utils import check_host_restriction, get_admin_from_token

router = APIRouter(prefix="/capabilities", tags=["Admin", "Capabilities"])


@router.get("")
async def get_admin_capabilities(request: Request) -> Dict[str, Any]:
    """
    Return the availability state of every artifacts feature for admins,
    including a top-level summary of how many features are available versus
    unavailable and why.
    """
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

    try:
        capabilities = get_artifact_capabilities()
        capabilities_dict = {
            feature: capability_to_dict(info) for feature, info in capabilities.items()
        }

        available = [
            feature
            for feature, info in capabilities.items()
            if info.state == CapabilityState.AVAILABLE
        ]
        unavailable = [
            feature
            for feature, info in capabilities.items()
            if info.state != CapabilityState.AVAILABLE
        ]

        summary = {
            "total": len(capabilities),
            "available_count": len(available),
            "unavailable_count": len(unavailable),
            "available": available,
            "unavailable": unavailable,
            "by_state": {
                state.value: [
                    feature
                    for feature, info in capabilities.items()
                    if info.state == state
                ]
                for state in CapabilityState
            },
        }

        return {
            "capabilities": capabilities_dict,
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"Failed to compute admin capabilities: {e}", exc_info=True)
        raise
