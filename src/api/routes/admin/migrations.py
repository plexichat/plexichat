"""
Admin panel routes for migration management.

Provides endpoints for viewing, running, and rolling back migrations
with special handling for irreversible migrations and emergency overrides.
"""

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import json

from fastapi import APIRouter, HTTPException, Depends, status as http_status, Request
from pydantic import BaseModel, Field

import utils.config as config
import utils.logger as logger
from src.core.migrations import validator
from src.core.migrations.manager import MigrationManager
from .utils import (
    check_host_restriction,
    get_admin_from_token,
    require_admin_permission,
    require_approved_request,
    validate_migration_version,
)
from src.api.schemas.common import ErrorResponse

router = APIRouter()


# The status snapshot at the heart of this endpoint is expensive to assemble on
# a cold process: get_migration_status() creates the tracker schema, reads the
# history tables and checksums the pending migration files, and every version's
# metadata is parsed from its source file before the irreversible-delay gate
# can be evaluated. On a quiet machine that first assembly measured ~200ms;
# every later poll was single-digit milliseconds.
#
# Admin dashboards poll this endpoint repeatedly, so keep the fully assembled
# response in a small, short-TTL, process-local cache. It is keyed by database
# identity (never by the per-request MigrationManager instance) so two databases
# in one process cannot share a snapshot, and the TTL is deliberately shorter
# than the core 15s status cache so this read-only view is never staler than
# core's own cache. A startup lifespan primes it so the first real request does
# not pay the cold cost. Caching only the response cannot weaken the auth, host
# or permission gates: those run in the route before the cache is consulted,
# and the execution paths re-evaluate every gate when a migration is actually
# run or rolled back.
_LIST_CACHE_TTL_SECONDS = 5.0
_LIST_CACHE_MAX = 8
_list_cache: Dict[str, Tuple[float, "MigrationListResponse"]] = {}
_list_cache_lock = threading.Lock()


def _clear_migration_list_cache() -> None:
    """Drop cached list responses (tests and tooling)."""
    with _list_cache_lock:
        _list_cache.clear()


def _migration_list_cache_key(manager: MigrationManager) -> Optional[str]:
    """Stable, DB-scoped cache key (never the per-request manager identity).

    Returns None for unknown/mocked DB objects so tests with synthetic managers
    never share a cached snapshot with another test (or with production).
    """
    db = getattr(manager, "db", None)
    cfg = getattr(db, "config", None)
    if not isinstance(cfg, dict):
        return None
    db_type = getattr(db, "type", None)
    if not isinstance(db_type, str):
        db_type = str(cfg.get("type", ""))
    if db_type == "sqlite":
        scope = "sqlite:" + os.path.abspath(
            os.path.expanduser(str(cfg.get("path", "")))
        )
    elif db_type == "postgres":
        pg = cfg.get("postgres", {}) or {}
        scope = (
            f"postgres:{pg.get('host', '')}:{pg.get('port', '')}:{pg.get('dbname', '')}"
        )
    else:
        return None
    delay = config.get("database.migrations.irreversible_migration_delay_days", 7)
    return f"{scope}|delay={delay}"


def _get_cached_list(key: str) -> Optional["MigrationListResponse"]:
    now = time.monotonic()
    with _list_cache_lock:
        entry = _list_cache.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires > now:
            return value
        _list_cache.pop(key, None)
        return None


def _set_cached_list(key: str, value: "MigrationListResponse") -> None:
    now = time.monotonic()
    with _list_cache_lock:
        if len(_list_cache) >= _LIST_CACHE_MAX:
            for stale_key in [k for k, (exp, _) in _list_cache.items() if exp <= now]:
                _list_cache.pop(stale_key, None)
            while len(_list_cache) >= _LIST_CACHE_MAX:
                _list_cache.pop(next(iter(_list_cache)))
        _list_cache[key] = (now + _LIST_CACHE_TTL_SECONDS, value)


class MigrationInfo(BaseModel):
    """Migration information model."""

    version: str
    name: str
    status: str
    is_irreversible: bool
    can_run: bool
    can_run_reason: Optional[str]
    applied_at: Optional[str]
    depends_on: Optional[List[str]]
    metadata: Optional[Dict[str, Any]]


class MigrationListResponse(BaseModel):
    """Response model for migration list."""

    migrations: List[MigrationInfo]
    applied_count: int
    pending_count: int
    failed_count: int


def _applied_at(value: Any) -> Optional[str]:
    """Render a tracker timestamp as an ISO 8601 string.

    SQLite hands back the stored text, PostgreSQL a ``datetime``. The response
    model takes a string, so a native datetime used to fail validation and turn
    ``GET /admin/migrations/{version}`` into a 500 on PostgreSQL only.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class MigrationDetailResponse(BaseModel):
    """Response model for migration details."""

    version: str
    name: str
    status: str
    is_irreversible: bool
    can_run: bool
    can_run_reason: Optional[str]
    applied_at: Optional[str]
    execution_time_ms: Optional[int]
    depends_on: Optional[List[str]]
    metadata: Optional[Dict[str, Any]]
    logs: List[Dict[str, Any]]


class MigrationRunRequest(BaseModel):
    """Request model for running a migration."""

    dry_run: bool = False
    confirmation_text: Optional[str] = None


class MigrationRunResponse(BaseModel):
    """Response model for migration run result."""

    success: bool
    version: str
    message: str
    dry_run: bool
    execution_time_ms: Optional[int]


class DestructiveActionRequest(BaseModel):
    """Explicit confirmation for a destructive maintenance action."""

    confirmation: str | None = None


class EmergencyOverrideRequest(BaseModel):
    """Request model for emergency override."""

    # The reason is written to the audit log and rendered in the admin UI, so
    # bound it; the override lifetime is capped at one day so a fat-fingered
    # request cannot mint a long-lived bypass token.
    reason: str = Field(..., min_length=1, max_length=500)
    expires_minutes: int = Field(30, ge=1, le=1440)


class EmergencyOverrideResponse(BaseModel):
    """Response model for emergency override."""

    token: str
    expires_at: str
    message: str


def get_migration_manager() -> MigrationManager:
    """Dependency injection for migration manager."""
    import src.api as api

    db = api.get_db()
    if db is None:
        raise RuntimeError("Database not initialized")
    return MigrationManager(db)


def _admin_has_live_scope(admin_id) -> bool:
    """Confirm an admin session still carries a live granted scope.

    A role edit/revocation deliberately leaves the row in ``admin_sessions``
    intact: admin routes re-read roles on every request rather than revoking
    the session. The migration read routes used a merely-valid admin session
    (via ``get_admin_user`` -> ``get_admin_from_token``) as sufficient
    authority to expose migration metadata, status and logs. A demoted admin
    whose roles were all revoked therefore kept that access until natural
    session expiry. Require at least one still-granted scope from a live role
    assignment; fail closed on a missing DB, malformed payload or lookup error.
    Only a literal boolean ``true`` grants.
    """
    try:
        import sys

        # Resolve through sys.modules: "import x.y as z" can return a stale
        # parent-package attribute after a test replaces the module, whereas
        # the sys.modules lookup always sees the live binding.
        api_module = sys.modules.get("src.api")
        if api_module is None:
            import src.api as api_module

        db = api_module.get_db()
        if db is None:
            return False
        rows = db.fetch_all(
            "SELECT r.permissions FROM admin_roles r "
            "JOIN admin_role_assignments a ON a.role_id = r.id "
            "WHERE a.admin_id = ?",
            (admin_id,),
        )
    except Exception:
        return False
    for row in rows or ():
        raw = (
            row.get("permissions")
            if isinstance(row, dict)
            else (row[0] if row else None)
        )
        permissions = raw
        if isinstance(raw, str):
            try:
                permissions = json.loads(raw)
            except (TypeError, ValueError):
                continue
        if isinstance(permissions, dict) and any(
            v is True for v in permissions.values()
        ):
            return True
    return False


def get_admin_user(request: Request) -> int:
    """Dependency: authenticate an admin and re-check its live authority.

    Session validity alone is not authority -- demotion leaves the session
    row intact. These read routes expose migration metadata/status/logs, so a
    surviving admin bearer must still carry a live granted scope.
    """
    admin_id = get_admin_from_token(request)
    if not _admin_has_live_scope(admin_id):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return admin_id


def validate_version(version: str) -> None:
    """Validate migration version format to prevent path traversal.

    Delegates to the shared strict validator so every version that reaches a
    migration module-loading sink is confined to exactly three ASCII digits.
    """
    validate_migration_version(version)


def _build_migration_list(manager: MigrationManager) -> MigrationListResponse:
    """Assemble the full migration list from the current status snapshot.

    Kept separate from the route so the startup warm-up can prime the same
    response the request path serves. No auth/host checks belong here: they
    are performed by the route before the cache is consulted.
    """
    status = manager.get_migration_status()
    applied = status["applied_migrations"]
    pending_versions = status["pending_migrations"]
    failed_versions = status["failed_migrations"]
    all_records = status["all_records"]

    # Get delay configuration
    delay_days = config.get("database.migrations.irreversible_migration_delay_days", 7)
    delay_hours = delay_days * 24

    migrations = []
    # Build the version index once. The old membership test rebuilt a list
    # of every record for every pending version (O(n^2)) on each request.
    record_versions = {record["version"] for record in all_records}
    metadata_by_version: Dict[str, Dict[str, Any]] = {}
    for record in all_records:
        version = record["version"]
        metadata = manager.tracker.get_migration_metadata(version)
        metadata_by_version[version] = metadata
        is_irreversible = metadata.get("irreversible", False)

        # Check if migration can be run. Reuse the metadata parsed above
        # so the gate does not look up/parse the same file a second time.
        can_run = True
        can_run_reason = None
        if record["status"] == "pending":
            can_run, can_run_reason = manager.tracker.can_run_irreversible_migration(
                version, delay_hours, metadata=metadata
            )

        migrations.append(
            MigrationInfo(
                version=version,
                name=record.get("name", ""),
                status=record["status"],
                is_irreversible=is_irreversible,
                can_run=can_run,
                can_run_reason=can_run_reason,
                applied_at=_applied_at(record.get("applied_at")),
                depends_on=metadata.get("depends_on"),
                metadata=metadata,
            )
        )

    # Add pending migrations not in records
    for version in pending_versions:
        if version not in record_versions:
            metadata = metadata_by_version.get(version)
            if metadata is None:
                metadata = manager.tracker.get_migration_metadata(version)
                metadata_by_version[version] = metadata
            is_irreversible = metadata.get("irreversible", False)
            can_run, can_run_reason = manager.tracker.can_run_irreversible_migration(
                version, delay_hours, metadata=metadata
            )

            migrations.append(
                MigrationInfo(
                    version=version,
                    name=metadata.get("description", ""),
                    status="pending",
                    is_irreversible=is_irreversible,
                    can_run=can_run,
                    can_run_reason=can_run_reason,
                    applied_at=None,
                    depends_on=metadata.get("depends_on"),
                    metadata=metadata,
                )
            )

    return MigrationListResponse(
        migrations=migrations,
        applied_count=len(applied),
        pending_count=len(pending_versions),
        failed_count=len(failed_versions),
    )


def _warm_migration_list_cache() -> None:
    """Prime the response (and the core status/metadata caches) off-request.

    Best-effort: a failure here must never break startup or a request. The
    route still builds and caches the response on demand if this did not run.
    """
    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            return
        manager = MigrationManager(db)
        key = _migration_list_cache_key(manager)
        if key is not None and _get_cached_list(key) is None:
            _set_cached_list(key, _build_migration_list(manager))
    except Exception as e:
        logger.debug(f"Migration list warm-up skipped: {e}")


@asynccontextmanager
async def _migration_list_lifespan(app: Any):
    # Run in a worker thread: the cold DB/schema/file work must not block the
    # event loop while the application is starting.
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, _warm_migration_list_cache
        )
    except Exception as e:
        logger.debug(f"Migration list warm-up failed: {e}")
    yield


# Merged into the application lifespan by FastAPI's include_router().
router.lifespan_context = _migration_list_lifespan


@router.get(
    "/migrations",
    response_model=MigrationListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_migrations(
    request: Request,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> MigrationListResponse:
    """
    List all migrations with their status.

    Returns applied, pending, and failed migrations with metadata.
    """
    # Security gates are intentionally outside the cache: every request still
    # passes host restriction, token authentication and the read-scope check
    # before a cached response can be returned.
    check_host_restriction(request)
    require_admin_permission(request, "admin.migrations.read")
    try:
        key = _migration_list_cache_key(manager)
        response = _get_cached_list(key) if key is not None else None
        if response is None:
            response = _build_migration_list(manager)
            if key is not None:
                _set_cached_list(key, response)
        return response
    except Exception as e:
        logger.error(f"Failed to list migrations: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list migrations",
        )


@router.get(
    "/migrations/status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_migration_system_status(
    request: Request,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> Dict[str, Any]:
    check_host_restriction(request)
    require_admin_permission(request, "admin.migrations.read")
    try:
        status = manager.get_migration_status()
        integrity = manager.validate_migration_integrity()
        manager.tracker.update_uptime()
        return {
            **status,
            "integrity": integrity,
            "uptime_tracked": True,
        }
    except Exception as e:
        logger.error(f"Failed to get migration system status: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get migration system status",
        )


@router.get(
    "/migrations/{version}",
    response_model=MigrationDetailResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid migration version"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Migration not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_migration_details(
    request: Request,
    version: str,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> MigrationDetailResponse:
    """
    Get detailed information about a specific migration.

    Includes metadata, status, logs, and execution details.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.migrations.read")
    validate_version(version)
    try:
        # Get migration status
        record = manager.tracker.get_migration_status(version)
        if not record:
            # Reuse the cached status snapshot rather than recomputing every
            # pending migration (checksum + dependency reads for the whole
            # directory) just to resolve one version's display name.
            pending_versions = manager.get_migration_status()["pending_migrations"]
            if version not in pending_versions:
                raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=f"Migration {version} not found",
                )

            name = version
            for candidate_version, file_path in validator.get_migration_files(
                str(manager.migrations_dir)
            ):
                if candidate_version == version:
                    stem = Path(file_path).stem
                    name = stem.split("_", 1)[1] if "_" in stem else stem
                    break

            metadata = manager.tracker.get_migration_metadata(version)
            delay_days = config.get(
                "database.migrations.irreversible_migration_delay_days", 7
            )
            delay_hours = delay_days * 24
            can_run, can_run_reason = manager.tracker.can_run_irreversible_migration(
                version, delay_hours, metadata=metadata
            )

            return MigrationDetailResponse(
                version=version,
                name=name,
                status="pending",
                is_irreversible=metadata.get("irreversible", False),
                can_run=can_run,
                can_run_reason=can_run_reason,
                applied_at=None,
                execution_time_ms=None,
                depends_on=metadata.get("depends_on"),
                metadata=metadata,
                logs=[],
            )

        metadata = manager.tracker.get_migration_metadata(version)
        delay_days = config.get(
            "database.migrations.irreversible_migration_delay_days", 7
        )
        delay_hours = delay_days * 24
        can_run, can_run_reason = manager.tracker.can_run_irreversible_migration(
            version, delay_hours, metadata=metadata
        )

        # Get migration logs
        logs = manager.tracker.get_migration_logs(version)

        return MigrationDetailResponse(
            version=version,
            name=record.get("name", ""),
            status=record["status"],
            is_irreversible=metadata.get("irreversible", False),
            can_run=can_run,
            can_run_reason=can_run_reason,
            applied_at=_applied_at(record.get("applied_at")),
            execution_time_ms=record.get("execution_time_ms"),
            depends_on=metadata.get("depends_on"),
            metadata=metadata,
            logs=logs,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get migration details: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get migration details",
        )


@router.post(
    "/migrations/{version}/run",
    response_model=MigrationRunResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def run_migration(
    request: Request,
    version: str,
    body: MigrationRunRequest,
    manager: MigrationManager = Depends(get_migration_manager),
) -> MigrationRunResponse:
    """
    Run a specific migration.

    Requires the ``admin.migrations.execute`` permission plus an approved
    approval request. For irreversible migrations, requires confirmation text
    matching "THE DATABASE IS BACKED UP" unless dry_run is True.
    """
    check_host_restriction(request)
    validate_version(version)
    admin_user = require_admin_permission(request, "admin.migrations.execute")
    require_approved_request("migrations.run")
    try:
        # Get migration metadata
        metadata = manager.tracker.get_migration_metadata(version)
        is_irreversible = metadata.get("irreversible", False)

        # Check confirmation for irreversible migrations
        if is_irreversible and not body.dry_run:
            if body.confirmation_text != "THE DATABASE IS BACKED UP":
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail='For irreversible migrations, confirmation text must be "THE DATABASE IS BACKED UP"',
                )

        # Check if migration can be run
        delay_days = config.get(
            "database.migrations.irreversible_migration_delay_days", 7
        )
        delay_hours = delay_days * 24
        can_run, can_run_reason = manager.tracker.can_run_irreversible_migration(
            version, delay_hours
        )

        if not can_run:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot run migration: {can_run_reason}",
            )

        # Run migration
        result = manager.apply_migration(version, dry_run=body.dry_run)

        logger.info(
            f"Migration {version} executed by admin {admin_user} (dry_run={body.dry_run})"
        )

        return MigrationRunResponse(
            success=result["success"],
            version=result["version"],
            message=result.get("message", ""),
            dry_run=result.get("dry_run", False),
            execution_time_ms=result.get("execution_time_ms"),
        )
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Internal server error",
        )
    except Exception as e:
        logger.error(f"Failed to run migration {version}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run migration",
        )


@router.post(
    "/migrations/{version}/rollback",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def rollback_migration(
    request: Request,
    version: str,
    body: DestructiveActionRequest | None = None,
    manager: MigrationManager = Depends(get_migration_manager),
) -> Dict[str, Any]:
    """
    Rollback a specific migration.

    Requires the elevated ``maintenance`` scope and ``admin.migrations.execute``
    permission, an approved approval request, and an explicit confirmation
    phrase. Only available for migrations with a down() function. Irreversible
    migrations cannot be rolled back.
    """
    check_host_restriction(request)
    validate_version(version)
    admin_user = require_admin_permission(request, "admin.migrations.execute")
    require_admin_permission(request, "maintenance")
    require_approved_request("migrations.rollback")
    # Imported here so the gate cannot be silently dropped by a rewrite of
    # the module-level import block.
    from .utils import require_destructive_confirmation

    require_destructive_confirmation(
        "migrations.rollback", body.confirmation if body else None
    )
    try:
        # Check if migration is irreversible
        metadata = manager.tracker.get_migration_metadata(version)
        if metadata.get("irreversible"):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Cannot rollback irreversible migration",
            )

        result = manager.rollback_migration(version)

        logger.info(f"Migration {version} rolled back by admin {admin_user}")

        return result
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Internal server error",
        )
    except Exception as e:
        logger.error(f"Failed to rollback migration {version}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rollback migration",
        )


@router.post(
    "/migrations/emergency-override",
    response_model=EmergencyOverrideResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def generate_emergency_override(
    request: Request,
    body: EmergencyOverrideRequest,
    manager: MigrationManager = Depends(get_migration_manager),
) -> EmergencyOverrideResponse:
    """
    Generate an emergency override token for bypassing migration delays.

    Requires the ``admin.migrations.execute`` permission AND an approved
    approval request (action ``migrations.emergency_override``) - minting a
    delay-bypass credential is precisely the class of action the approvals
    system exists for. The minted token is single-use: it is consumed by the
    first validation against it.

    The token must be set as the EMERGENCY_MIGRATION_OVERRIDE environment
    variable to take effect. Tokens expire after the specified time.
    """
    check_host_restriction(request)
    admin_user = require_admin_permission(request, "admin.migrations.execute")
    require_approved_request("migrations.emergency_override")
    try:
        token = manager.tracker.generate_emergency_override(
            reason=body.reason, expires_minutes=body.expires_minutes
        )

        logger.warning(
            f"Emergency override token generated by admin {admin_user} for: {body.reason}"
        )

        return EmergencyOverrideResponse(
            token=token,
            expires_at=f"{body.expires_minutes} minutes from now",
            message="Set this token as the EMERGENCY_MIGRATION_OVERRIDE environment variable to bypass migration delays",
        )
    except Exception as e:
        logger.error(f"Failed to generate emergency override: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate emergency override",
        )
