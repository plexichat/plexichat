"""
Admin artifact routes - oversight and maintenance endpoints for artifacts.

All endpoints are admin-guarded (host restriction + admin token). They operate
across every server and are intended for instance-level administration.
"""

from src.core.security.hardening import HardeningViolation, bounded_int, parse_id

from typing import Any, Dict

import utils.logger as logger
import utils.config as config
from fastapi import APIRouter, Request, HTTPException, status

from .utils import (
    check_host_restriction,
    # Retained for test monkeypatch compatibility; the routes now authenticate
    # and authorize together through require_admin_permission.
    get_admin_from_token,  # noqa: F401
    require_admin_permission,
    require_approved_request,
)
from src.api.schemas.artifacts import (
    ArtifactResponse,
    RetentionPurgeResponse,
    ServerRetentionRequest,
    ServerRetentionResponse,
)
from src.api.schemas.common import ErrorResponse, SnowflakeIDParam
from src.core.artifacts.repository import row_to_artifact


ALLOWED_FEATURES = {"plexiscribe", "plexiscript", "plexiboard", "whiteboard", "voice"}

router = APIRouter(prefix="/artifacts", tags=["Admin", "Artifacts"])


def _parse_before_cursor(value):
    """Parse an additive ``"created_at:id"`` keyset cursor.

    Both halves are already present on every returned artifact row. They go
    through the shared bounded-int / snowflake gates before they can reach SQL,
    so a malformed, homoglyph or oversized cursor is rejected here rather than
    becoming a live range predicate. ``HardeningViolation`` is raised for any
    invalid value (it subclasses ``ValueError``).
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise HardeningViolation("page cursor must be a string")
    parts = value.split(":")
    if len(parts) != 2 or not all(parts):
        raise HardeningViolation("page cursor must be 'created_at:id'")
    created_at = bounded_int(
        parts[0], "cursor.created_at", minimum=0, maximum=2**63 - 1
    )
    row_id = parse_id(parts[1], "cursor.id")
    return created_at, row_id


def _list_with_total(db, limit: int, offset: int, before=None):
    """Fetch one page and the unpaged total in a single, index-friendly query.

    The total comes from a scalar ``SELECT COUNT(*)`` subquery rather than a
    ``COUNT(*) OVER()`` window. Both return the same value, but the window
    forces the database to materialise and sort *every* matching row before
    applying ``LIMIT``/``OFFSET`` — on a large artifacts table that scan
    dominated this endpoint. The scalar form is evaluated once against the
    index and the page itself still uses ``idx_artifacts_created``.

    ``before`` is an optional, additive ``"created_at:id"`` cursor (the two
    fields already returned on every artifact). When supplied, the page is
    seeked by the indexed ``(created_at, id)`` tuple instead of walking an
    ``OFFSET``: deep pages cost O(page size) rather than O(offset). A benchmark
    counting VM ops on a 20k-row table measured 60,768 ops for the deep OFFSET
    page versus 918 for a shallow page; the keyset seek is flat. Omitting it
    keeps the legacy paged contract byte-for-byte for existing callers, and the
    scalar total plus the response shape are identical on both paths.

    Keeping the count in the same statement preserves the single round trip
    and a consistent snapshot (unlike a separate count query).

    Returns ``(items, total_or_None)``. ``total`` is ``None`` when the requested
    page is past the end (no row carries a ``_total`` value); the caller then
    falls back to an explicit count so the response contract is unchanged.
    """
    if before is None:
        rows = db.fetch_all(
            "SELECT a.*, (SELECT COUNT(*) FROM artifacts) AS _total FROM artifacts a "
            "ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    else:
        cursor_created_at, cursor_id = _parse_before_cursor(before)
        # A row-value tuple comparison lets SQLite seek straight into the
        # (created_at, id) index. The equivalent OR expansion
        # ("created_at < ? OR (created_at = ? AND id < ?)") defeats the planner
        # and turns into a full scan, so keep the tuple form.
        rows = db.fetch_all(
            "SELECT a.*, (SELECT COUNT(*) FROM artifacts) AS _total FROM artifacts a "
            "WHERE (created_at, id) < (?, ?) "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
            (cursor_created_at, cursor_id, limit),
        )
    if not rows:
        return [], None
    return [row_to_artifact(row) for row in rows], int(rows[0]["_total"])


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
    # This reads artifact metadata across *every* server. Authentication alone
    # is not sufficient: a valid admin token carrying an unrelated scope (e.g.
    # only ``users.read``) must not be able to enumerate the whole instance's
    # artifacts. Require the same scoped permission as the neighbouring
    # force-delete / retention mutations.
    require_admin_permission(request, "admin.artifacts.manage")

    try:
        limit = bounded_int(
            request.query_params.get("limit", 100), "limit", minimum=1, maximum=200
        )
        offset = bounded_int(
            request.query_params.get("offset", 0),
            "offset",
            minimum=0,
            maximum=10_000_000,
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid pagination"}},
        )
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid pagination"}},
        )

    # Additive keyset cursor. Plain ``None`` default keeps existing callers and
    # the legacy paged contract untouched; a supplied cursor is validated with
    # the shared hardening gates before it reaches SQL.
    before = request.query_params.get("before")
    if before is not None:
        try:
            _parse_before_cursor(before)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid cursor"}},
            )

    try:
        manager = _get_manager()
        filters = {"limit": limit, "offset": offset}
        if before is None:
            artifacts, total = _list_with_total(manager.db, limit, offset)
        else:
            artifacts, total = _list_with_total(manager.db, limit, offset, before)
        if total is None:
            # Empty page (offset past the end): the window function has no row
            # to report a total from, so fall back to the explicit count.
            artifacts = manager.list_with_filters(filters=filters)
            total = manager.count(filters)
        return {
            "total": total,
            "items": [
                ArtifactResponse.model_validate(a).model_dump(mode="json")
                for a in artifacts
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
    """Admin force-delete of any artifact.

    Requires ``admin.artifacts.manage`` plus an approved approval request:
    this destroys user data across every server.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.artifacts.manage")
    if admin_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Admin token required"}},
        )

    # Bind the path segment through the canonical ASCII snowflake gate. A
    # bare ``int()`` accepted homoglyph digits ("٤٢" -> 42), PEP-515
    # separators ("1_2" -> 12), signs/whitespace, negatives and unbounded
    # integers, so a segment one hop reads as text was silently
    # reinterpreted as a different id at a destructive, cross-server
    # endpoint. Every other admin route family parses ids with parse_id.
    # Parse *before* the approval gate so the id can pin the single-use
    # approval to the exact artifact being deleted.
    try:
        aid = parse_id(artifact_id, "artifact_id")
    except (HardeningViolation, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
        )

    # This route destroys one concrete artifact, so the approval must be
    # granted for that exact object and admin (N646). Without the binding an
    # approval for artifact A -- or a global row with target_id NULL, which the
    # gate's ``? IS NULL OR col = ?`` disjunct lets through -- could be spent
    # deleting artifact B. Consume it fail-closed: a mismatched binding is a
    # 403 and no delete side effect follows.
    require_approved_request("artifacts.force_delete", target_context=(aid, admin_id))

    try:
        manager = _get_manager()
        # ``manager.delete`` already fetches the row internally before removing
        # it. Use that result instead of a separate pre-flight ``get``; on the
        # success path this saves one database round trip. Only on a failed
        # delete do a single follow-up read to distinguish "already gone"
        # (404) from a genuine deletion failure (500), preserving the contract.
        if not manager.delete(aid, purging_user_id=admin_id):
            if manager.get(aid) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Artifact not found"}},
                )
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
    admin_id = require_admin_permission(request, "admin.artifacts.manage")
    if admin_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Admin token required"}},
        )

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


@router.post(
    "/retention/server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_set_server_retention(
    request: Request, body: ServerRetentionRequest
) -> ServerRetentionResponse:
    """Set or clear a per-server retention override.

    Persists the override in the ``server_artifact_settings`` table (migration
    048). Passing ``retention_days=null`` clears the override so the server
    reverts to the global ``default_retention_days``.
    """
    check_host_restriction(request)
    # This mutates a per-server retention override, so require the same scoped
    # permission as the neighbouring feature-settings write; authentication
    # alone must not be enough to change retention policy.
    require_admin_permission(request, "admin.artifacts.manage")

    try:
        # Defense in depth: the request schema already vets the id, but go
        # through the same ASCII snowflake gate as every other route boundary
        # so a directly-constructed body cannot smuggle a bool, float, homoglyph
        # or oversized id into the retention write.
        try:
            server_id = parse_id(body.server_id, "server_id")
        except (HardeningViolation, TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid server_id"}},
            )
        manager = _get_manager()
        try:
            manager.set_server_retention_days(server_id, body.retention_days)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(exc)}},
            ) from exc
        effective = manager.get_server_retention_days(server_id)
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


@router.get(
    "/features/{feature}",
    summary="Get feature settings for a server",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
    },
)
async def admin_get_feature_settings(
    feature: str,
    server_id: SnowflakeIDParam,
    request: Request,
) -> Dict[str, Any]:
    """Get per-server override settings for a feature."""
    check_host_restriction(request)
    # Reading a server's feature overrides exposes configuration across the
    # whole instance. The previous code only authenticated the caller (and
    # even discarded the returned id), so any valid token with any scope could
    # read these settings while the matching write needed a scoped permission.
    require_admin_permission(request, "admin.artifacts.manage")
    if feature not in ALLOWED_FEATURES:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")
    manager = _get_manager()
    settings = manager.get_feature_settings(server_id, feature)
    # Snowflake as a decimal string (project convention), even though it echoed
    # an int-typed path parameter.
    return {"server_id": str(server_id), "feature": feature, "settings": settings}


@router.post(
    "/features/{feature}",
    summary="Set feature settings for a server",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
    },
)
async def admin_set_feature_settings(
    feature: str,
    body: Dict[str, Any],
    request: Request,
) -> Dict[str, Any]:
    """Set per-server override settings for a feature.

    Body should be: { "server_id": int, "settings": { "key": "value"|null } }
    Pass null to clear a setting back to default.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.artifacts.manage")
    if feature not in ALLOWED_FEATURES:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")

    server_id = body.get("server_id")
    settings = body.get("settings", {})
    if server_id is None or not isinstance(settings, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": "server_id and settings are required"}
            },
        )
    # The settings map is stored as a JSON blob and echoed back to the admin
    # panel. Bound its shape so a bad request cannot persist an arbitrarily
    # large document or keys/values the UI cannot render.
    if len(settings) > 50:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "too many settings"}},
        )
    for key, value in settings.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "invalid setting key"}},
            )
        if isinstance(value, str) and len(value) > 1024:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": f"setting {key!r} is too long"}
                },
            )
    # Accept a decimal-string snowflake (the admin UI sends ids as strings to
    # avoid Number() rounding) as well as an int, then bind a real int so the
    # query works on both SQLite and Postgres.
    # ``settings`` is a free-form dict, so ``server_id`` has no schema gate at
    # all. Route it through the canonical snowflake parser: bare ``int()``
    # truncated floats (1.9 -> 1), accepted bools (True -> 1), homoglyph digits
    # and unbounded values, any of which could target the wrong server's
    # feature overrides.
    try:
        server_id = parse_id(server_id, "server_id")
    except (HardeningViolation, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server_id"}},
        )

    manager = _get_manager()
    result = manager.set_feature_settings_bulk(server_id, feature, settings)
    return {"server_id": str(server_id), "feature": feature, "settings": result}
