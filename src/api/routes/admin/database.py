"""
Admin database monitoring routes.
"""

from fastapi import APIRouter, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from .utils import (
    check_host_restriction,
    require_admin_permission,
    require_approved_request,
    require_destructive_confirmation,
    validate_migration_version,
)
from src.api.schemas.common import ErrorResponse
import src.api as api
import utils.logger as logger
from src.core.migrations.manager import MigrationManager

router = APIRouter()


@router.get(
    "/database/pool-health",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_db_pool_health(request: Request):
    """
    Retrieve the current health and connection pool statistics for the database.

    Provides information on active, idle, and total connections.
    """
    check_host_restriction(request)
    require_admin_permission(request, "maintenance")
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        stats = db.get_pool_stats()
        return stats
    except Exception as e:
        logger.error(f"DB pool health error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/database/migrations/status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_migration_status(request: Request):
    """
    Get current migration status including applied, pending, and failed migrations.
    """
    check_host_restriction(request)
    require_admin_permission(request, "maintenance")
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        manager = MigrationManager(db)
        status = manager.get_migration_status()
        return status
    except Exception as e:
        logger.error(f"Migration status error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/database/migrations/integrity",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def validate_migration_integrity(request: Request):
    """
    Validate integrity of applied migrations by comparing checksums.
    """
    check_host_restriction(request)
    require_admin_permission(request, "maintenance")
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        manager = MigrationManager(db)
        result = manager.validate_migration_integrity()
        return result
    except Exception as e:
        logger.error(f"Migration integrity check error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/database/migrations/apply-all",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def apply_all_migrations(
    request: Request, dry_run: bool = False, confirmation: str | None = None
):
    """
    Apply all pending migrations.

    Args:
        dry_run: If True, simulate migration without applying changes
        confirmation: Exact phrase required for a real (non-dry-run) apply
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.migrations.execute")
    # Elevated scope + explicit confirmation on top of the approval gate.
    require_admin_permission(request, "maintenance")
    require_approved_request("migrations.apply_all")
    require_destructive_confirmation(
        "migrations.apply_all", confirmation, dry_run=dry_run
    )
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        manager = MigrationManager(db)
        # Migration discovery/execution is synchronous and may touch many
        # files; keep it off the event loop so concurrent admin requests do
        # not queue behind it.
        result = await run_in_threadpool(manager.apply_all_pending, dry_run=dry_run)
        return result
    except Exception as e:
        logger.error(f"Apply migrations error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/database/migrations/apply/{version}",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def apply_migration(
    request: Request,
    version: str,
    dry_run: bool = False,
    confirmation: str | None = None,
):
    """
    Apply a specific migration by version.

    Args:
        version: Migration version (e.g., '001')
        dry_run: If True, simulate migration without applying changes
        confirmation: Exact phrase required for a real (non-dry-run) apply
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.migrations.execute")
    # Elevated scope + explicit confirmation on top of the approval gate.
    require_admin_permission(request, "maintenance")
    require_approved_request("migrations.run")
    require_destructive_confirmation("migrations.apply", confirmation, dry_run=dry_run)
    # The version flows into the migration manager, which loads a Python
    # module by file path. Confine it to three digits before that sink.
    validate_migration_version(version)
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        manager = MigrationManager(db)
        result = await run_in_threadpool(
            manager.apply_migration, version, dry_run=dry_run
        )
        return result
    except Exception as e:
        logger.error(f"Apply migration {version} error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/database/migrations/rollback/{version}",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def rollback_migration(
    request: Request, version: str, confirmation: str | None = None
):
    """
    Rollback a specific migration by version.

    Args:
        version: Migration version to rollback (e.g., '001')
        confirmation: Exact phrase required to authorize the rollback
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.migrations.execute")
    # Elevated scope + explicit confirmation on top of the approval gate.
    require_admin_permission(request, "maintenance")
    require_approved_request("migrations.rollback")
    require_destructive_confirmation("migrations.rollback", confirmation)
    validate_migration_version(version)
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        manager = MigrationManager(db)
        result = await run_in_threadpool(manager.rollback_migration, version)
        return result
    except Exception as e:
        logger.error(f"Rollback migration {version} error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
