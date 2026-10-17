"""
Push notification module - Mobile push notifications via FCM/APNs with token management.
"""

from .manager import PushManager

__all__ = ["PushManager"]

_manager = None
_setup_complete = False


def setup(db, notifications_module=None):
    """
    Initialize the push module.

    Args:
        db: Database instance (must be connected)
        notifications_module: Optional notifications module for user settings
    """
    global _manager, _setup_complete

    _manager = PushManager(db, notifications_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError("Push module not initialized. Call push.setup(db) first.")
    return _manager
