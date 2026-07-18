"""
Account lifecycle management module.

This module handles account deletion scheduling, cancellation, delay,
and forced purging operations.
"""

from typing import Optional
from ._lazy import _get_auth_manager


def schedule_account_deletion(
    user_id: int, password: str, totp_code: Optional[str] = None
) -> bool:
    """Schedule an account for deletion."""
    return (
        _get_auth_manager()
        .get_instance()
        .schedule_account_deletion(user_id, password, totp_code)
    )


def cancel_account_deletion(user_id: int, admin_id: Optional[int] = None) -> bool:
    """Cancel a scheduled account deletion."""
    return _get_auth_manager().get_instance().cancel_account_deletion(user_id, admin_id)


def delay_account_deletion(
    user_id: int, additional_days: int, admin_id: Optional[int] = None
) -> bool:
    """Extend the deletion grace period for a scheduled account deletion."""
    return (
        _get_auth_manager()
        .get_instance()
        .delay_account_deletion(user_id, additional_days, admin_id)
    )


def force_purge_account(user_id: int, admin_id: Optional[int] = None) -> bool:
    """Immediately purge a user account, bypassing the grace period."""
    return _get_auth_manager().get_instance().force_purge_account(user_id, admin_id)


__all__ = [
    "schedule_account_deletion",
    "cancel_account_deletion",
    "delay_account_deletion",
    "force_purge_account",
]
