"""
Capabilities routes - expose per-feature availability state to clients.

Clients use this to render notices/banners about which artifacts features are
currently usable (e.g. editor, whiteboard, voice transcription/recording).

``details`` are scrubbed to launcher-safe scalars (see
``src/core/suite/registry/service.py``): the client only reads
``state``/``message``, so raw evaluator internals such as the transcription
``provider`` are dropped here. Admins who need full diagnostics use
``GET /admin/capabilities``, which intentionally stays raw.

The response is additionally bounded: a registry glitch must not turn this
small launcher endpoint into an unbounded JSON document, and the optional
filters accept only declared enum values and bounded strings.
"""

import time
from enum import Enum
from typing import Annotated, Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Query

import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.artifacts.capabilities import (
    CapabilityState,
    get_artifact_capabilities,
    capability_to_dict,
)
from src.core.security.hardening import reject_control_characters
from src.core.suite.registry.service import scrub_details

router = APIRouter(prefix="/capabilities/artifacts", tags=["Capabilities"])


class CapabilityStateFilter(str, Enum):
    """Client-facing filter values; mirrors the declared CapabilityState enum."""

    AVAILABLE = CapabilityState.AVAILABLE.value
    EVALUATION = CapabilityState.EVALUATION.value
    DISABLED_BY_CONFIG = CapabilityState.DISABLED_BY_CONFIG.value
    DISABLED_BY_LICENSE = CapabilityState.DISABLED_BY_LICENSE.value
    DEPENDENCY_MISSING = CapabilityState.DEPENDENCY_MISSING.value
    MISCONFIGURED = CapabilityState.MISCONFIGURED.value


MAX_FEATURE_FILTER_LENGTH = 128
MAX_CAPABILITY_KEYS = 100
MAX_MESSAGE_LENGTH = 1000
MAX_DETAIL_KEYS = 20
MAX_DETAIL_VALUE_LENGTH = 512

_ALLOWED_STATES = frozenset(state.value for state in CapabilityStateFilter)


# Evaluating the whole catalog re-runs every capability evaluator, and the
# voice-transcription evaluator imports the provider factory and probes the
# backend. That work is identical for every caller and only changes when the
# license/config changes (the core already memoizes single-feature lookups for
# 5s). Cache the fully serialized, scrubbed map for the same short TTL so a
# burst of launcher polls collapses into one evaluation. A short TTL bounds
# staleness after a license hot-swap; filters are still applied per request.
_CAPABILITIES_PAYLOAD_TTL_SECONDS = 5.0
_capabilities_payload_cache: Optional[Tuple[float, Dict[str, Any]]] = None


def _build_capabilities_payload() -> Dict[str, Any]:
    """Evaluate and serialize the full capability catalog."""
    capabilities = get_artifact_capabilities()
    result: Dict[str, Any] = {}
    for name, info in capabilities.items():
        payload = capability_to_dict(info)
        payload["details"] = scrub_details(payload.get("details"))
        result[name] = payload
    return result


def _get_capabilities_payload() -> Dict[str, Any]:
    """Return the current serialized catalog, recomputing at most every TTL."""
    global _capabilities_payload_cache
    now = time.monotonic()
    cached = _capabilities_payload_cache
    if cached is not None and (now - cached[0]) < _CAPABILITIES_PAYLOAD_TTL_SECONDS:
        return cached[1]
    payload = _build_capabilities_payload()
    _capabilities_payload_cache = (now, payload)
    return payload


def clear_capabilities_payload_cache() -> None:
    """Drop the memoized payload (call after a license/config change)."""
    global _capabilities_payload_cache
    _capabilities_payload_cache = None


def bound_capabilities(
    payload: Dict[str, Any],
    *,
    feature: Optional[str] = None,
    state: Optional[CapabilityStateFilter] = None,
) -> Dict[str, Any]:
    """Bound and optionally filter the launcher capability map.

    Direct callers (and tests) pass plain ``None`` defaults; FastAPI supplies
    the validated query values over HTTP.
    """
    if not isinstance(payload, dict):
        return {}

    wanted_feature = feature if isinstance(feature, str) else None
    if wanted_feature is not None:
        try:
            wanted_feature = reject_control_characters(
                wanted_feature, "feature"
            ).strip()
        except ValueError:
            return {}
        if not wanted_feature or len(wanted_feature) > MAX_FEATURE_FILTER_LENGTH:
            return {}

    wanted_state: Optional[str] = None
    if isinstance(state, Enum):
        wanted_state = state.value
    elif isinstance(state, str):
        wanted_state = state
    if wanted_state is not None and wanted_state not in _ALLOWED_STATES:
        return {}

    result: Dict[str, Any] = {}
    for name, info in list(payload.items())[:MAX_CAPABILITY_KEYS]:
        if (
            not isinstance(name, str)
            or not name
            or len(name) > MAX_FEATURE_FILTER_LENGTH
        ):
            continue
        if not isinstance(info, dict):
            continue
        info_state = info.get("state")
        state_value = info_state.value if isinstance(info_state, Enum) else info_state
        if state_value not in _ALLOWED_STATES:
            continue
        if wanted_feature is not None and name != wanted_feature:
            continue
        if wanted_state is not None and state_value != wanted_state:
            continue

        safe = dict(info)
        message = safe.get("message")
        if isinstance(message, str):
            safe["message"] = message[:MAX_MESSAGE_LENGTH]
        details = safe.get("details")
        if isinstance(details, dict):
            scrubbed = scrub_details(details)
            safe["details"] = {
                str(key)[:MAX_FEATURE_FILTER_LENGTH]: (
                    value[:MAX_DETAIL_VALUE_LENGTH] if isinstance(value, str) else value
                )
                for key, value in list(scrubbed.items())[:MAX_DETAIL_KEYS]
            }
        result[name] = safe
    return result


@router.get(
    "",
    operation_id="get_artifact_capabilities_route",
    summary="Get artifact feature capabilities",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_capabilities(
    current_user: TokenInfo = Depends(get_current_user),
    feature: Annotated[
        Optional[str], Query(max_length=MAX_FEATURE_FILTER_LENGTH)
    ] = None,
    state: Annotated[Optional[CapabilityStateFilter], Query()] = None,
) -> Dict[str, Any]:
    """
    Return the availability state of every artifacts feature for the current
    user. Used by the client to show availability notices.

    ``feature`` and ``state`` are optional, bounded filters; an invalid enum
    value is rejected by FastAPI before the handler runs.
    """
    try:
        # The catalog is user-independent; ``bound_capabilities`` applies the
        # per-request filters to copies, so the cached map is never mutated.
        payload = _get_capabilities_payload()
        return bound_capabilities(payload, feature=feature, state=state)
    except Exception as e:
        logger.error(f"Failed to compute capabilities: {e}", exc_info=True)
        raise
