"""
Audit logging module.

This module handles retrieval of login history and security events.
"""

from typing import List
from .models import AuditEntry
from ._lazy import _get_auth_manager


def get_login_history(user_id: int, limit: int = 50) -> List[AuditEntry]:
    """Get login history for a user."""
    return _get_auth_manager().get_instance().get_login_history(user_id, limit)


def get_security_events(user_id: int, limit: int = 50) -> List[AuditEntry]:
    """Get security events for a user."""
    return _get_auth_manager().get_instance().get_security_events(user_id, limit)


__all__ = [
    "get_login_history",
    "get_security_events",
]
