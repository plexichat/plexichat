"""
Admin artifact routes - oversight and maintenance endpoints for artifacts.

All endpoints are admin-guarded (host restriction + admin token). They operate
across every server and are intended for instance-level administration.
"""

from typing import Any, Dict

import utils.logger as logger
import utils.config as config
from fastapi import APIRouter, Request, HTTPException, status

from .utils import check_host_restriction, get_admin_from_token
from src.api.schemas.artifacts import (
    ArtifactResponse,
    RetentionPurgeResponse,
    ServerRetentionRequest,
    ServerRetentionResponse,
)


router = APIRouter(prefix="/artifacts", tags=["Admin", "Artifacts"])


def _get_manager():
    import src.api as api_mod

    db = api_mod.get_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Database unavailable"}},
        )
    from src.core.artifacts.manager import ArtifactManager

    artifacts_cfg = config.get("artifacts", {}) or {}
    return ArtifactManager(db, artifacts_cfg)


@router.get("")
async def admin_list_artifacts(request: Request) -> Dict[str, Any]:
    """List all artifacts across every server (admin oversight)."""
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

    try:
        manager = _get_manager()
        artifacts = manager.list_with_filters(filters={})
        return {
            "total": len(artifacts),
            "items": [
                ArtifactResponse.model_validate(a).model_dump() for a in artifacts
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin list artifacts failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete("/{artifact_id}")
async def admin_delete_artifact(artifact_id: str, request: Request) -> Dict[str, Any]:
    """Admin force-delete of any artifact."""
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        if manager.get(aid) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )
        if not manager.delete(aid):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to delete"}},
            )
        return {"success": True, "id": str(aid)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin delete artifact failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post("/retention/purge")
async def admin_purge_expired(request: Request) -> RetentionPurgeResponse:
    """Trigger a retention purge of expired artifacts."""
    check_host_restriction(request)
    get_admin_from_token(request)

    try:
        import src.api as api_mod

        db = api_mod.get_db()
        if db is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Database unavailable"}},
            )
        from src.core.artifacts.retention import purge_expired

        purged = purge_expired(db, config.get("artifacts", {}) or {})
        return RetentionPurgeResponse(purged=purged)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin retention purge failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post("/retention/server")
async def admin_set_server_retention(
    request: Request, body: ServerRetentionRequest
) -> ServerRetentionResponse:
    """Set or clear a per-server retention override.

    Persists the override in the ``server_artifact_settings`` table (migration
    048). Passing ``retention_days=null`` clears the override so the server
    reverts to the global ``default_retention_days``.
    """
    check_host_restriction(request)
    get_admin_from_token(request)

    try:
        manager = _get_manager()
        manager.set_server_retention_days(body.server_id, body.retention_days)
        effective = manager.get_server_retention_days(body.server_id)
        return ServerRetentionResponse(
            server_id=body.server_id, retention_days=effective, success=True
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin set server retention failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
