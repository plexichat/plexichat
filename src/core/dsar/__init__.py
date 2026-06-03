"""
DSAR (Data Subject Access Request) module - GDPR Article 20 Right to Data Portability.

This module provides a complete system for users to request and receive exports
of all their personal data, in compliance with GDPR Article 20 (Right to Data Portability).

Features:
- Request data exports in JSON or ZIP format
- Admin approval workflow for sensitive data
- Background worker for automatic processing
- Hash-chained audit log for compliance
- Envelope encryption for exported files
- Automatic expiration and cleanup

Usage:
    # In main.py (setup once)
    from src.core.dsar import setup
    from src.core.database import Database

    db = Database()
    db.connect()
    setup(db)

    # In any other file
    from src.core.dsar import request_data_export, get_user_requests, get_request_status

    # Request a data export
    request = request_data_export(user_id, format='json')

    # Check status
    status = get_request_status(request.id, user_id)

    # Get export file when ready
    export_file = get_export_file(request.id, user_id)
"""

from typing import Optional, List, Dict, Any

from .models import DSARStatus, DSARRequest, ExportManifest
from .schema import create_tables as _create_tables_schema
from .audit_log import DSARLog
from .collector import DataCollector
from .export_formats import ExportFormatGenerator
from .harvester import DSARHarvester
from .manager import DSARManager

_module_manager: Optional[DSARManager] = None
_setup_complete = False


def setup(db) -> None:
    """
    Initialize the DSAR module.

    Args:
        db: Database instance (must be connected)
    """
    global _module_manager, _setup_complete

    create_tables(db)

    _module_manager = DSARManager(db)
    _setup_complete = True


def is_setup() -> bool:
    """Check if the DSAR module is initialized."""
    return _setup_complete


def _get_manager() -> DSARManager:
    """Get the DSAR manager, ensuring setup was called."""
    if not _setup_complete:
        raise RuntimeError("DSAR module not initialized. Call dsar.setup(db) first.")
    assert _module_manager is not None
    return _module_manager


def create_tables(db) -> None:
    """
    Create DSAR tables in the database.
    Usually called automatically by setup().
    """
    _create_tables_schema(db)


def get_dsar(request_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get a DSAR request by ID for a specific user.
    """
    return _get_manager().get_request_status(request_id, user_id)


def request_data_export(
    user_id: int, format: str = "json", categories: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Request a new data export for a user.

    Args:
        user_id: The user requesting their data
        format: Export format ('json' or 'zip')
        categories: Optional list of specific categories to export

    Returns:
        The created DSAR request, or None if the manager could not
        materialise it (treated by callers as a generic failure).
    """
    return _get_manager().request_export(user_id, format, categories)


def get_user_requests(user_id: int) -> List[Dict[str, Any]]:
    """
    Get all DSAR requests for a user.

    Args:
        user_id: The user ID

    Returns:
        List of DSAR requests
    """
    return _get_manager().get_user_requests(user_id)


def get_request_status(request_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get the status of a specific DSAR request.

    Args:
        request_id: The request ID
        user_id: The user ID (for authorization)

    Returns:
        The request details or None if not found
    """
    return _get_manager().get_request_status(request_id, user_id)


def cancel_request(request_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Cancel a pending DSAR request.

    Args:
        request_id: The request ID
        user_id: The user ID (for authorization)

    Returns:
        The updated request, or None if it could not be loaded.
    """
    return _get_manager().cancel_request(request_id, user_id)


def get_export_file(request_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get the export file for a ready DSAR request.

    Args:
        request_id: The request ID
        user_id: The user ID (for authorization)

    Returns:
        Dict with file_path, checksum, file_size or None if not ready
    """
    return _get_manager().get_export_file(request_id, user_id)


def approve_request(request_id: int, admin_id: int) -> Optional[Dict[str, Any]]:
    """
    Admin approve a pending DSAR request.

    Args:
        request_id: The request ID
        admin_id: The admin approving the request

    Returns:
        The updated request, or None if it could not be loaded.
    """
    return _get_manager().approve_request(request_id, admin_id)


def deny_request(
    request_id: int, admin_id: int, reason: str
) -> Optional[Dict[str, Any]]:
    """
    Admin deny a pending DSAR request.

    Args:
        request_id: The request ID
        admin_id: The admin denying the request
        reason: The denial reason

    Returns:
        The updated request, or None if it could not be loaded.
    """
    return _get_manager().deny_request(request_id, admin_id, reason)


def get_admin_requests(
    status: Optional[str] = None, limit: int = 50, offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Admin list all DSAR requests with optional filtering.

    Args:
        status: Optional status filter
        limit: Maximum number of results
        offset: Offset for pagination

    Returns:
        List of DSAR requests
    """
    return _get_manager().get_admin_requests(status, limit, offset)


def generate_manual(request_id: int, admin_id: int) -> Optional[Dict[str, Any]]:
    """
    Admin manually trigger generation for a DSAR request.

    Args:
        request_id: The request ID
        admin_id: The admin triggering generation

    Returns:
        The updated request, or None if it could not be loaded.
    """
    return _get_manager().generate_manual(request_id, admin_id)


__all__ = [
    "setup",
    "is_setup",
    "create_tables",
    "get_dsar",
    "request_data_export",
    "get_user_requests",
    "get_request_status",
    "cancel_request",
    "get_export_file",
    "approve_request",
    "deny_request",
    "get_admin_requests",
    "generate_manual",
    "DSARStatus",
    "DSARRequest",
    "ExportManifest",
    "DSARLog",
    "DataCollector",
    "ExportFormatGenerator",
    "DSARHarvester",
    "DSARManager",
]
