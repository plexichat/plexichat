"""
Health check endpoint.
"""

from typing import Any, Dict, Optional

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
    events_metrics: Optional[Dict[str, Any]] = None
    is_queue_recreate_pending: Optional[bool] = None


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def health_check() -> HealthResponse:
    """Check API health status.

    Returns the current health status, API version, and — when
    available — observable EventManager saturation metrics
    (``events_metrics``: dropped-event count, subscribers,
    queue size/max, critical-subscribers). Also exposes
    ``is_queue_recreate_pending`` so operators can detect
    uvicorn reloads / closed-loop drift before the next dispatch
    migrates the queue.

    Events metrics are ADVISORY — ``status`` + ``version`` are
    the contract /health consumers actually depend on, and a
    bug in the events layer must NOT take this endpoint down.
    Each metric is wrapped in its own try/except so partial
    success returns one without dropping both. We log the
    fallbacks at DEBUG (not WARNING) because they are an
    EXPECTED transient during cold-boot before
    ``events.setup(...)`` has populated ``src.core.events.event_manager``
    — a real production warning here would imply a deployment
    misorder, but the path is harmless and self-heals as soon
    as the first dispatch lands. ``logger.debug`` uses lazy
    ``%s`` formatting; eager f-strings would force string
    construction even when DEBUG is disabled.
    """
    try:
        try:
            ver = version.current_string()
        except RuntimeError:
            ver = "unknown"

        events_metrics: Optional[Dict[str, Any]] = None
        recreate_pending: Optional[bool] = None
        try:
            # Import ONCE — both blocks below share the cached
            # module object so a re-entrant / partial-init failure
            # surfaces in one place, not twice. Hoist keeps partial
            # success: if the import raises, both metrics return
            # None with a single log line; if one method call
            # raises, the other can still succeed.
            from src.core.events import event_manager  # type: ignore[attr-defined]  # populated by events.setup() at runtime

            try:
                events_metrics = event_manager.get_loss_metrics()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Health check: event_manager.get_loss_metrics() failed: %s",
                    exc,
                )
            try:
                recreate_pending = event_manager.is_queue_recreate_pending()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Health check: event_manager.is_queue_recreate_pending() failed: %s",
                    exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Health check: event_manager import failed: %s",
                exc,
            )

        return HealthResponse(
            status="healthy",
            version=ver,
            events_metrics=events_metrics,
            is_queue_recreate_pending=recreate_pending,
        )
    except Exception as e:
        logger.error("Health check failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
