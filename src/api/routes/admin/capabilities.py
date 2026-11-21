"""
Admin capabilities routes - expose per-feature availability state to admins.

Adds a top-level summary (counts of available vs. unavailable features) on top
of the per-feature capability breakdown used by the client endpoints.

Unlike the public ``GET /capabilities/artifacts`` route (which scrubs
``details`` to launcher-safe scalars), this admin route intentionally returns
raw evaluator details such as the transcription ``provider``: it is
host-restricted and gated on the documented ``config.read`` scope, because the
payload is derived from system configuration and the license document. A valid
admin session alone must not be enough to read this cached snapshot -- the
``summary`` counts and per-feature config details are configuration data that
other reads gate behind ``config.read``.

The catalog is memoised for a short TTL: evaluating every capability re-runs
the voice-transcription provider probe, which is identical for every caller.
The response is also bounded so a registry/config glitch cannot turn this
diagnostic endpoint into an unbounded JSON document.
"""

import time
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request, HTTPException, status

import utils.logger as logger
from src.core.artifacts.capabilities import (
    CapabilityState,
    get_artifact_capabilities,
    capability_to_dict,
)
from .utils import (
    check_host_restriction,
    # Kept for existing test doubles that patch this module attribute.
    get_admin_from_token,  # noqa: F401
    require_admin_permission,
)
from src.api.schemas.common import ErrorResponse

router = APIRouter(prefix="/capabilities", tags=["Admin", "Capabilities"])

# Bounds keep the diagnostic payload finite. They are generous enough that a
# normal catalog is returned byte-for-byte unchanged.
MAX_CAPABILITIES = 100
MAX_FEATURE_LENGTH = 128
MAX_MESSAGE_LENGTH = 1000
MAX_DETAIL_KEYS = 20
MAX_DETAIL_VALUE_LENGTH = 512

# Short TTL bounds staleness after a license/config hot-swap while collapsing
# a burst of admin-panel polls into one evaluation.
_CAPABILITIES_TTL_SECONDS = 5.0
_payload_cache: Optional[Tuple[float, Dict[str, Any]]] = None


def clear_admin_capabilities_payload_cache() -> None:
    """Drop the memoized admin payload (call after a license/config change)."""
    global _payload_cache
    _payload_cache = None


def warm_admin_capabilities_payload_cache() -> None:
    """Prime the admin catalog off the request path (best-effort, never fatal).

    Shares the cold voice-transcription provider probe with the launcher
    catalog. The host-restriction and ``config.read`` scope gates in
    ``get_admin_capabilities`` still run on every request; only the shared,
    admin-independent payload is primed here. Failures are swallowed so boot can
    never depend on them.
    """
    try:
        _get_payload()
    except Exception as e:
        logger.debug(f"Admin capability catalog warm-up skipped: {e}")


def _build_payload() -> Dict[str, Any]:
    """Evaluate and serialize the bounded raw catalog + summary."""
    capabilities = get_artifact_capabilities()

    capabilities_dict: Dict[str, Any] = {}
    for feature, info in list(capabilities.items())[:MAX_CAPABILITIES]:
        if (
            not isinstance(feature, str)
            or not feature
            or len(feature) > MAX_FEATURE_LENGTH
        ):
            continue
        payload = capability_to_dict(info)
        message = payload.get("message")
        if isinstance(message, str):
            payload["message"] = message[:MAX_MESSAGE_LENGTH]
        details = payload.get("details")
        if isinstance(details, dict):
            payload["details"] = {
                str(key)[:MAX_FEATURE_LENGTH]: (
                    value[:MAX_DETAIL_VALUE_LENGTH] if isinstance(value, str) else value
                )
                for key, value in list(details.items())[:MAX_DETAIL_KEYS]
            }
        capabilities_dict[feature] = payload

    # Extract each feature's state string once. The old summary helper
    # re-read ``capabilities[feature].state`` and re-converted the enum for
    # every comparison: available, unavailable, and once per enum member in
    # ``by_state`` -- roughly O(states * features) lookups for a map that
    # never changes during this build. One pass keeps the output identical.
    state_by_feature: Dict[str, Any] = {}
    for feature in capabilities_dict:
        state = capabilities[feature].state
        state_by_feature[feature] = (
            state.value if isinstance(state, CapabilityState) else state
        )

    available = [
        f
        for f, state_value in state_by_feature.items()
        if state_value == CapabilityState.AVAILABLE.value
    ]
    unavailable = [
        f
        for f, state_value in state_by_feature.items()
        if state_value != CapabilityState.AVAILABLE.value
    ]

    summary = {
        "total": len(capabilities_dict),
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "available": available,
        "unavailable": unavailable,
        "by_state": {
            state.value: [
                f
                for f, state_value in state_by_feature.items()
                if state_value == state.value
            ]
            for state in CapabilityState
        },
    }
    return {"capabilities": capabilities_dict, "summary": summary}


def _get_payload() -> Dict[str, Any]:
    """Return the current admin payload, recomputing at most every TTL."""
    global _payload_cache
    now = time.monotonic()
    cached = _payload_cache
    if cached is not None and (now - cached[0]) < _CAPABILITIES_TTL_SECONDS:
        return cached[1]
    payload = _build_payload()
    _payload_cache = (now, payload)
    return payload


@router.get(
    "",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_admin_capabilities(request: Request) -> Dict[str, Any]:
    """
    Return the availability state of every artifacts feature for admins,
    including a top-level summary of how many features are available versus
    unavailable and why.
    """
    # Security gates stay first: host restriction + the documented config-read
    # scope. Session validation alone let any admin (including a tickets-only
    # support role) read this cached config/license snapshot. Both gates run on
    # every request, before the short-TTL payload cache is consulted.
    check_host_restriction(request)
    require_admin_permission(request, "config.read")

    try:
        return _get_payload()
    except Exception as e:
        logger.error(f"Failed to compute admin capabilities: {e}", exc_info=True)
        # Never leak the raw exception (paths/config internals) to the client.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
