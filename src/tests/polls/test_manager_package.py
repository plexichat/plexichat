"""Package-boundary tests for the polls manager split."""

from __future__ import annotations

from src.core.polls.manager import PollManager, _is_unique_violation
from src.core.polls.manager.base import PollManagerBase

PUBLIC_METHODS = (
    "create_poll",
    "get_poll",
    "vote",
    "change_vote",
    "get_results",
    "close_poll",
    "has_voted",
    "remove_vote",
    "get_voters",
    "get_user_votes",
    "delete_poll",
    "check_expired_polls",
)


def test_public_surface_is_unchanged() -> None:
    assert issubclass(PollManager, PollManagerBase)
    for name in PUBLIC_METHODS:
        assert callable(getattr(PollManager, name)), name


def test_unique_violation_probe_is_reexported() -> None:
    assert _is_unique_violation(Exception("UNIQUE constraint failed")) is True
    assert _is_unique_violation(Exception("connection reset")) is False
