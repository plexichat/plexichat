"""
Poll models - Dataclasses for all poll-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum
from src.core.base import SnowflakeID


class PollResultsVisibility(Enum):
    """When poll results are visible."""

    ALWAYS = "always"
    AFTER_VOTE = "after_vote"
    AFTER_END = "after_end"


@dataclass
class PollOption:
    """Represents a poll option."""

    id: SnowflakeID
    poll_id: SnowflakeID
    text: str
    position: int = 0
    vote_count: int = 0


@dataclass
class Poll:
    """Represents a poll attached to a message."""

    id: SnowflakeID
    message_id: SnowflakeID
    question: str
    created_by: SnowflakeID
    created_at: int
    ends_at: Optional[int] = None
    ended_at: Optional[int] = None
    allow_multiple_choice: bool = False
    results_visibility: PollResultsVisibility = PollResultsVisibility.ALWAYS
    options: List[PollOption] = field(default_factory=list)
    total_votes: int = 0
    is_ended: bool = False


@dataclass
class PollVote:
    """Represents a user's vote on a poll."""

    id: SnowflakeID
    poll_id: SnowflakeID
    option_id: SnowflakeID
    user_id: SnowflakeID
    voted_at: int


@dataclass
class PollResults:
    """Poll results with vote counts and percentages."""

    poll: Poll
    options: List[PollOption]
    total_votes: int
    user_voted: bool = False
    user_votes: List[SnowflakeID] = field(default_factory=list)
