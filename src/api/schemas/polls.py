"""
Poll schemas - Request/response models for poll endpoints.
"""

from typing import Literal, Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

from .common import SnowflakeID, reject_unsafe_ui_text

ResultsVisibility = Literal["always", "after_vote", "after_end"]


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
    results_visibility: ResultsVisibility = Field(
        "always", description="When results are visible (always, after_vote, after_end)"
    )

    @field_validator("question")
    @classmethod
    def _no_ui_xss_chars(cls, value: str, info) -> str:
        return reject_unsafe_ui_text(value, info.field_name)

    @field_validator("options")
    @classmethod
    def _no_ui_xss_chars_options(cls, value: List[str], info) -> List[str]:
        checked: List[str] = []
        for i, opt in enumerate(value):
            if len(opt) > 100:
                raise ValueError(
                    f"{info.field_name}[{i}] must be at most 100 characters"
                )
            checked.append(reject_unsafe_ui_text(opt, f"{info.field_name}[{i}]"))
        return checked

    @model_validator(mode="after")
    def _options_are_distinct(self) -> "PollInlineCreateRequest":
        """Reject duplicate options that only differ in case or padding.

        Two options such as ``"Maybe"`` and ``" maybe "`` render as distinct
        choices but are indistinguishable to a voter, so the result count can
        be split between what is effectively one answer. Field-level length
        and character checks cannot see that relationship between options.
        """
        seen: set[str] = set()
        for i, opt in enumerate(self.options):
            normalized = opt.strip().casefold()
            if normalized in seen:
                raise ValueError(f"options[{i}] duplicates an earlier option")
            seen.add(normalized)
        return self


class PollCreateRequest(PollInlineCreateRequest):
    """Poll creation request."""

    message_id: SnowflakeID = Field(..., description="Message ID to attach poll to")


class PollVoteRequest(BaseModel):
    """Poll vote request."""

    model_config = ConfigDict(from_attributes=True)

    option_ids: List[SnowflakeID] = Field(
        ..., min_length=1, max_length=10, description="Selected option IDs"
    )

    @model_validator(mode="after")
    def _option_ids_are_distinct(self) -> "PollVoteRequest":
        """Reject a ballot that names the same option more than once.

        SnowflakeID normalizes its input to a canonical decimal string, so
        ``["123", "00123"]`` is caught here. Without this check a single vote
        could count several times for one option, inflating the tally.
        """
        if len(set(self.option_ids)) != len(self.option_ids):
            raise ValueError("option_ids must not contain duplicates")
        return self


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
    total_votes: Optional[int] = Field(None, description="Total votes")
    is_ended: bool = Field(False, description="Whether poll has ended")


class PollResultsResponse(BaseModel):
    """Poll results response."""

    model_config = ConfigDict(from_attributes=True)

    poll: PollResponse = Field(..., description="Poll info")
    options: List[PollOptionResponse] = Field(
        default_factory=list, description="Options with counts"
    )
    total_votes: Optional[int] = Field(None, description="Total votes")
    user_voted: bool = Field(False, description="Whether user voted")
    user_votes: List[SnowflakeID] = Field(
        default_factory=list, description="Option IDs voted by user"
    )
