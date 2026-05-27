"""
Admin panel routes for migration management.

Provides endpoints for viewing, running, and rolling back migrations
with special handling for irreversible migrations and emergency overrides.
"""

import re
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, status as http_status, Request
from pydantic import BaseModel

import utils.config as config
import utils.logger as logger
from src.core.migrations.manager import MigrationManager
from src.core.database import Database
from .utils import check_host_restriction, get_admin_from_token

# Valid migration version pattern (3 digits)
VERSION_PATTERN = re.compile(r"^\d{3}$")

router = APIRouter()


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


class EmergencyOverrideRequest(BaseModel):
    """Request model for emergency override."""

    reason: str
    expires_minutes: int = 30


class EmergencyOverrideResponse(BaseModel):
    """Response model for emergency override."""

    token: str
    expires_at: str
    message: str


def get_migration_manager() -> MigrationManager:
    """Dependency injection for migration manager."""
    db = Database()
    return MigrationManager(db)


def get_admin_user(request: Request) -> int:
    """Dependency injection for admin user with proper authentication."""
    return get_admin_from_token(request)


def validate_version(version: str) -> None:
    """Validate migration version format to prevent path traversal."""
    if not VERSION_PATTERN.match(version):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid migration version format. Expected 3 digits (e.g., '001', '025')",
        )


@router.get("/migrations", response_model=MigrationListResponse)
async def list_migrations(
    request: Request,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> MigrationListResponse:
    """
    List all migrations with their status.

    Returns applied, pending, and failed migrations with metadata.
    """
    check_host_restriction(request)
    try:
        status = manager.get_migration_status()
        applied = status["applied_migrations"]
        pending_versions = status["pending_migrations"]
        failed_versions = status["failed_migrations"]
        all_records = status["all_records"]

        # Get delay configuration
        delay_days = config.get(
            "database.migrations.irreversible_migration_delay_days", 7
        )
        delay_hours = delay_days * 24

        migrations = []
        for record in all_records:
            version = record["version"]
            metadata = manager.tracker.get_migration_metadata(version)
            is_irreversible = metadata.get("irreversible", False)

            # Check if migration can be run
            can_run = True
            can_run_reason = None
            if record["status"] == "pending":
                can_run, can_run_reason = (
                    manager.tracker.can_run_irreversible_migration(version, delay_hours)
                )

            migrations.append(
                MigrationInfo(
                    version=version,
                    name=record.get("name", ""),
                    status=record["status"],
                    is_irreversible=is_irreversible,
                    can_run=can_run,
                    can_run_reason=can_run_reason,
                    applied_at=record.get("applied_at"),
                    depends_on=metadata.get("depends_on"),
                    metadata=metadata,
                )
            )

        # Add pending migrations not in records
        for version in pending_versions:
            if version not in [r["version"] for r in all_records]:
                metadata = manager.tracker.get_migration_metadata(version)
                is_irreversible = metadata.get("irreversible", False)
                can_run, can_run_reason = (
                    manager.tracker.can_run_irreversible_migration(version, delay_hours)
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
    except Exception as e:
        logger.error(f"Failed to list migrations: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list migrations: {str(e)}",
        )


@router.get("/migrations/status")
async def get_migration_system_status(
    request: Request,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> Dict[str, Any]:
    check_host_restriction(request)
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
            detail=f"Failed to get migration system status: {str(e)}",
        )


@router.get("/migrations/{version}", response_model=MigrationDetailResponse)
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
    validate_version(version)
    try:
        # Get migration status
        record = manager.tracker.get_migration_status(version)
        if not record:
            # Check if it's a pending migration
            pending = manager.get_pending_migrations()
            migration = None
            for m in pending:
                if m.version == version:
                    migration = m
                    break

            if not migration:
                raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=f"Migration {version} not found",
                )

            metadata = manager.tracker.get_migration_metadata(version)
            delay_days = config.get(
                "database.migrations.irreversible_migration_delay_days", 7
            )
            delay_hours = delay_days * 24
            can_run, can_run_reason = manager.tracker.can_run_irreversible_migration(
                version, delay_hours
            )

            return MigrationDetailResponse(
                version=version,
                name=migration.name,
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
            version, delay_hours
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
            applied_at=record.get("applied_at"),
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
            detail=f"Failed to get migration details: {str(e)}",
        )


@router.post("/migrations/{version}/run", response_model=MigrationRunResponse)
async def run_migration(
    request: Request,
    version: str,
    body: MigrationRunRequest,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> MigrationRunResponse:
    """
    Run a specific migration.

    For irreversible migrations, requires confirmation text matching
    "THE DATABASE IS BACKED UP" unless dry_run is True.
    """
    check_host_restriction(request)
    validate_version(version)
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
    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to run migration {version}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to run migration: {str(e)}",
        )


@router.post("/migrations/{version}/rollback")
async def rollback_migration(
    request: Request,
    version: str,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> Dict[str, Any]:
    """
    Rollback a specific migration.

    Only available for migrations with a down() function.
    Irreversible migrations cannot be rolled back.
    """
    check_host_restriction(request)
    validate_version(version)
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
    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to rollback migration {version}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rollback migration: {str(e)}",
        )


@router.post("/migrations/emergency-override", response_model=EmergencyOverrideResponse)
async def generate_emergency_override(
    request: Request,
    body: EmergencyOverrideRequest,
    manager: MigrationManager = Depends(get_migration_manager),
    admin_user: int = Depends(get_admin_user),
) -> EmergencyOverrideResponse:
    """
    Generate an emergency override token for bypassing migration delays.

    The token must be set as the EMERGENCY_MIGRATION_OVERRIDE environment
    variable to take effect. Tokens expire after the specified time.
    """
    check_host_restriction(request)
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
            detail=f"Failed to generate emergency override: {str(e)}",
        )
