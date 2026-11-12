"""Polls manager package.

This package replaces the former single-file ``manager.py``. The public
surface is intentionally unchanged: importing :class:`PollManager` from
``src.core.polls.manager`` keeps working for every existing caller, while
the internals are split into one mixin per responsibility.

``_is_unique_violation`` is re-exported for callers that used it as the
driver-error probe before the split.
"""

from .base import _is_unique_violation as _is_unique_violation
from .creation import CreationMixin
from .lifecycle import LifecycleMixin
from .reading import ReadingMixin
from .rosters import RosterMixin
from .voting import VotingMixin


class PollManager(
    CreationMixin,
    ReadingMixin,
    VotingMixin,
    RosterMixin,
    LifecycleMixin,
):
    """Core poll manager handling all operations."""


__all__ = ["PollManager"]
