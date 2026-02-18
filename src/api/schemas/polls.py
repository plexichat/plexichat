"""
Poll schemas - Request/response models for poll endpoints.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class PollInlineCreateRequest(BaseModel):
    """Poll data embedded in a message creation request."""

    model_config = ConfigDict(from_attributes=True)

    question: str = Field(
        ..., min_length=1, max_length=300, description="Poll question"
    )
    options: List[str] = Field(
        ..., min_length=2, max_length=10, description="Poll options"
    )
    duration_hours: Optional[int] = Field(
        None, ge=1, le=168, description="Poll duration in hours"
    )
    allow_multiple_choice: bool = Field(
        False, description="Whether multiple options can be selected"
    )
    results_visibility: str = Field(
        "always", description="When results are visible (always, after_vote, after_end)"
    )


class PollCreateRequest(PollInlineCreateRequest):
    """Poll creation request."""

    message_id: SnowflakeID = Field(..., description="Message ID to attach poll to")


class PollVoteRequest(BaseModel):
    """Poll vote request."""

    model_config = ConfigDict(from_attributes=True)

    option_ids: List[SnowflakeID] = Field(
        ..., min_length=1, description="Selected option IDs"
    )


class PollOptionResponse(BaseModel):
    """Poll option response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Option ID")
    poll_id: SnowflakeID = Field(..., description="Poll ID")
    text: str = Field(..., description="Option text")
    position: int = Field(..., description="Option position")
    vote_count: Optional[int] = Field(None, description="Vote count")


class PollResponse(BaseModel):
    """Poll response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Poll ID")
    message_id: SnowflakeID = Field(..., description="Message ID")
    question: str = Field(..., description="Poll question")
    created_by: SnowflakeID = Field(..., description="Creator user ID")
    created_at: int = Field(..., description="Creation timestamp")
    ends_at: Optional[int] = Field(None, description="End timestamp")
    ended_at: Optional[int] = Field(None, description="Ended timestamp")
    allow_multiple_choice: bool = Field(
        False, description="Whether multiple options can be selected"
    )
    results_visibility: str = Field(
        "always", description="When results are visible (always, after_vote, after_end)"
    )
    options: List[PollOptionResponse] = Field(
        default_factory=list, description="Poll options"
    )
    total_votes: int = Field(0, description="Total votes")
    is_ended: bool = Field(False, description="Whether poll has ended")


class PollResultsResponse(BaseModel):
    """Poll results response."""

    model_config = ConfigDict(from_attributes=True)

    poll: PollResponse = Field(..., description="Poll info")
    options: List[PollOptionResponse] = Field(
        default_factory=list, description="Options with counts"
    )
    total_votes: int = Field(0, description="Total votes")
    user_voted: bool = Field(False, description="Whether user voted")
    user_votes: List[SnowflakeID] = Field(
        default_factory=list, description="Option IDs voted by user"
    )
