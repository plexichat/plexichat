"""
Threads module - Zero-friction API for thread management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import threads
    threads.setup(db, auth, messaging, servers, notifications)

    # In any other file (use directly)
    from src.core import threads
    thread = threads.create_thread(user_id=1, channel_id=123, name="Discussion")
"""

from typing import Optional, List, Dict, Any

from .models import (
    Thread,
    ThreadMember,
    ThreadType,
    AutoArchiveDuration,
    ThreadState,
)
from .exceptions import (
    ThreadError,
    ThreadNotFoundError,
    ThreadAccessDeniedError,
    ThreadArchivedError,
    ThreadLockedError,
    ThreadMemberNotFoundError,
    ThreadMemberExistsError,
    ThreadNameError,
    MessageNotFoundError,
    ChannelNotFoundError,
    PermissionDeniedError,
    InvalidThreadTypeError,
)

__all__ = [
    # Models
    "Thread",
    "ThreadMember",
    "ThreadType",
    "AutoArchiveDuration",
    "ThreadState",
    # Exceptions
    "ThreadError",
    "ThreadNotFoundError",
    "ThreadAccessDeniedError",
    "ThreadArchivedError",
    "ThreadLockedError",
    "ThreadMemberNotFoundError",
    "ThreadMemberExistsError",
    "ThreadNameError",
    "MessageNotFoundError",
    "ChannelNotFoundError",
    "PermissionDeniedError",
    "InvalidThreadTypeError",
    # Setup
    "setup",
    # Thread creation
    "create_thread",
    "create_thread_from_message",
    # Thread membership
    "join_thread",
    "leave_thread",
    "add_member",
    "remove_member",
    "get_thread_members",
    # Thread messages
    "send_message",
    "get_messages",
    "get_message_count",
    # Thread state
    "archive_thread",
    "unarchive_thread",
    "lock_thread",
    "unlock_thread",
    # Thread listing
    "get_active_threads",
    "get_archived_threads",
    "get_user_threads",
    "get_user_private_threads",
    # Thread info
    "get_thread",
    "update_thread",
    "delete_thread",
    # Permission checks
    "can_view_thread",
    "can_send_in_thread",
    "can_manage_thread",
]

_manager = None
_setup_complete = False


def setup(
    db,
    auth_module=None,
    messaging_module=None,
    servers_module=None,
    notifications_module=None,
):
    """
    Initialize the threads module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for user verification
        messaging_module: Optional messaging module for thread messages
        servers_module: Optional servers module for channel/permission checks
        notifications_module: Optional notifications module for thread mentions
    """
    global _manager, _setup_complete

    from .manager import ThreadManager

    _manager = ThreadManager(
        db, auth_module, messaging_module, servers_module, notifications_module
    )
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Threads module not initialized. Call threads.setup(db) first."
        )
    return _manager


# === Thread Creation ===


def create_thread(
    user_id: int,
    channel_id: int,
    name: str,
    thread_type: ThreadType = ThreadType.PUBLIC,
    auto_archive_duration: AutoArchiveDuration = AutoArchiveDuration.ONE_DAY,
) -> Thread:
    """Create a new thread in a channel (forum-style, no parent message)."""
    return _get_manager().create_thread(
        user_id, channel_id, name, thread_type, auto_archive_duration
    )


def create_thread_from_message(
    user_id: int,
    message_id: int,
    name: str,
    thread_type: ThreadType = ThreadType.PUBLIC,
    auto_archive_duration: AutoArchiveDuration = AutoArchiveDuration.ONE_DAY,
) -> Thread:
    """Create a thread from an existing message (reply thread)."""
    return _get_manager().create_thread_from_message(
        user_id, message_id, name, thread_type, auto_archive_duration
    )


# === Thread Membership ===


def join_thread(user_id: int, thread_id: int) -> ThreadMember:
    """Join a thread."""
    return _get_manager().join_thread(user_id, thread_id)


def leave_thread(user_id: int, thread_id: int) -> bool:
    """Leave a thread."""
    return _get_manager().leave_thread(user_id, thread_id)


def add_member(user_id: int, thread_id: int, member_user_id: int) -> ThreadMember:
    """Add a member to a thread."""
    return _get_manager().add_member(user_id, thread_id, member_user_id)


def remove_member(user_id: int, thread_id: int, member_user_id: int) -> bool:
    """Remove a member from a thread."""
    return _get_manager().remove_member(user_id, thread_id, member_user_id)


def get_thread_members(
    user_id: int, thread_id: int, limit: int = 100, after_user_id: Optional[int] = None
) -> List[ThreadMember]:
    """Get members of a thread."""
    return _get_manager().get_thread_members(user_id, thread_id, limit, after_user_id)


# === Thread Messages ===


def send_message(
    user_id: int,
    thread_id: int,
    content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Send a message to a thread."""
    return _get_manager().send_message(user_id, thread_id, content, attachments)


def get_messages(
    user_id: int,
    thread_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    after_id: Optional[int] = None,
) -> List[Any]:
    """Get messages from a thread."""
    return _get_manager().get_messages(user_id, thread_id, limit, before_id, after_id)


def get_message_count(thread_id: int) -> int:
    """Get the message count for a thread."""
    return _get_manager().get_message_count(thread_id)


# === Thread State ===


def archive_thread(user_id: int, thread_id: int) -> Thread:
    """Archive a thread."""
    return _get_manager().archive_thread(user_id, thread_id)


def unarchive_thread(user_id: int, thread_id: int) -> Thread:
    """Unarchive a thread."""
    return _get_manager().unarchive_thread(user_id, thread_id)


def lock_thread(user_id: int, thread_id: int) -> Thread:
    """Lock a thread (prevent new messages)."""
    return _get_manager().lock_thread(user_id, thread_id)


def unlock_thread(user_id: int, thread_id: int) -> Thread:
    """Unlock a thread."""
    return _get_manager().unlock_thread(user_id, thread_id)


# === Thread Listing ===


def get_active_threads(user_id: int, channel_id: int) -> List[Thread]:
    """Get active threads in a channel."""
    return _get_manager().get_active_threads(user_id, channel_id)


def get_archived_threads(
    user_id: int,
    channel_id: int,
    limit: int = 50,
    before_timestamp: Optional[int] = None,
) -> List[Thread]:
    """Get archived threads in a channel (paginated)."""
    return _get_manager().get_archived_threads(
        user_id, channel_id, limit, before_timestamp
    )


def get_user_threads(user_id: int, include_archived: bool = False) -> List[Thread]:
    """Get threads the user has joined."""
    return _get_manager().get_user_threads(user_id, include_archived)


def get_user_private_threads(
    user_id: int, channel_id: Optional[int] = None
) -> List[Thread]:
    """Get private threads the user is a member of."""
    return _get_manager().get_user_private_threads(user_id, channel_id)


# === Thread Info ===


def get_thread(user_id: int, thread_id: int) -> Optional[Thread]:
    """Get thread information."""
    return _get_manager().get_thread(user_id, thread_id)


def update_thread(
    user_id: int,
    thread_id: int,
    name: Optional[str] = None,
    auto_archive_duration: Optional[AutoArchiveDuration] = None,
) -> Thread:
    """Update thread settings."""
    return _get_manager().update_thread(user_id, thread_id, name, auto_archive_duration)


def delete_thread(user_id: int, thread_id: int) -> bool:
    """Delete a thread."""
    return _get_manager().delete_thread(user_id, thread_id)


# === Permission Checks ===


def can_view_thread(user_id: int, thread_id: int) -> bool:
    """Check if user can view a thread."""
    return _get_manager().can_view_thread(user_id, thread_id)


def can_send_in_thread(user_id: int, thread_id: int) -> bool:
    """Check if user can send messages in a thread."""
    return _get_manager().can_send_in_thread(user_id, thread_id)


def can_manage_thread(user_id: int, thread_id: int) -> bool:
    """Check if user can manage a thread (archive, lock, delete)."""
    return _get_manager().can_manage_thread(user_id, thread_id)
