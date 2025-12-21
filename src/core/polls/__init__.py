"""
Polls module - Zero-friction API for message polls.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import polls
    polls.setup(db, messaging)

    # In any other file (use directly)
    from src.core import polls
    poll = polls.create_poll(user_id, message_id, "What's your favorite?", ["A", "B", "C"])
"""

from typing import Optional, List

from .models import (
    Poll,
    PollOption,
    PollVote,
    PollResults,
    PollResultsVisibility,
)
from .exceptions import (
    PollError,
    PollNotFoundError,
    PollOptionNotFoundError,
    PollEndedError,
    PollNotEndedError,
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
    PermissionDeniedError,
    MessageNotFoundError,
)

__all__ = [
    "Poll",
    "PollOption",
    "PollVote",
    "PollResults",
    "PollResultsVisibility",
    "PollError",
    "PollNotFoundError",
    "PollOptionNotFoundError",
    "PollEndedError",
    "PollNotEndedError",
    "InvalidPollQuestionError",
    "InvalidPollOptionError",
    "PollOptionLimitError",
    "InvalidPollDurationError",
    "AlreadyVotedError",
    "MultipleVoteNotAllowedError",
    "PermissionDeniedError",
    "MessageNotFoundError",
    "setup",
    "create_poll",
    "get_poll",
    "vote",
    "get_results",
    "close_poll",
    "delete_poll",
    "check_expired_polls",
]

_manager = None
_setup_complete = False


def setup(db, messaging_module=None):
    """
    Initialize the polls module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for message access
    """
    global _manager, _setup_complete

    from .manager import PollManager

    _manager = PollManager(db, messaging_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Polls module not initialized. Call polls.setup(db) first."
        )
    return _manager


def create_poll(
    user_id: int,
    message_id: int,
    question: str,
    options: List[str],
    duration_hours: Optional[int] = None,
    allow_multiple_choice: bool = False,
    results_visibility: PollResultsVisibility = PollResultsVisibility.ALWAYS,
) -> Poll:
    """Create a new poll attached to a message."""
    return _get_manager().create_poll(
        user_id, message_id, question, options, duration_hours,
        allow_multiple_choice, results_visibility
    )


def get_poll(poll_id: int, user_id: int) -> Optional[Poll]:
    """Get a poll by ID."""
    return _get_manager().get_poll(poll_id, user_id)


def vote(user_id: int, poll_id: int, option_ids: List[int]) -> PollResults:
    """Vote on a poll."""
    return _get_manager().vote(user_id, poll_id, option_ids)


def get_results(poll_id: int, user_id: int) -> PollResults:
    """Get poll results."""
    return _get_manager().get_results(poll_id, user_id)


def close_poll(user_id: int, poll_id: int) -> Poll:
    """Close a poll early (creator only)."""
    return _get_manager().close_poll(user_id, poll_id)


def delete_poll(user_id: int, poll_id: int) -> bool:
    """Delete a poll (creator only)."""
    return _get_manager().delete_poll(user_id, poll_id)


def check_expired_polls() -> int:
    """Check for and end expired polls."""
    return _get_manager().check_expired_polls()
