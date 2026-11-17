"""Regression: request ids are bounded snowflakes, not arbitrary integers.

Python ints have no upper bound, so an unvalidated ``"9" * 30`` id reached
lookups and cache keys as a perfectly valid integer. Snowflakes are 64-bit;
the shared guard now caps every id field at the canonical maximum.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.common import (
    SnowflakeID,
    SnowflakeIDParam,
    SNOWFLAKE_MAX,
    snowflake_from_str,
    snowflake_to_str,
)
from src.api.schemas.reports import MessageReportRequest, UserReportRequest
from src.api.schemas.soundboard import SoundPlayRequest

OVERSIZED = SNOWFLAKE_MAX + 1


@pytest.mark.parametrize("value", [OVERSIZED, str(OVERSIZED), "9" * 30])
def test_snowflake_types_reject_oversized_ids(value):
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeID).validate_python(value)
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeIDParam).validate_python(value)


def test_snowflake_helpers_reject_oversized_ids():
    with pytest.raises(ValueError):
        snowflake_to_str(OVERSIZED)
    with pytest.raises(ValueError):
        snowflake_to_str(str(OVERSIZED))
    with pytest.raises(ValueError):
        snowflake_from_str(OVERSIZED)
    with pytest.raises(ValueError):
        snowflake_from_str(str(OVERSIZED))


def test_snowflake_boundary_values_are_accepted():
    assert TypeAdapter(SnowflakeID).validate_python(SNOWFLAKE_MAX) == str(SNOWFLAKE_MAX)
    assert snowflake_to_str(SNOWFLAKE_MAX) == str(SNOWFLAKE_MAX)
    assert snowflake_from_str(str(SNOWFLAKE_MAX)) == SNOWFLAKE_MAX


def test_request_model_id_fields_are_range_validated():
    # Previously bare ``str`` fields: now snowflakes with the same guard.
    with pytest.raises(ValidationError):
        MessageReportRequest(  # pyright: ignore[reportCallIssue]
            message_id=str(OVERSIZED),
            channel_id="123",
            reason="spam",
        )
    with pytest.raises(ValidationError):
        UserReportRequest(user_id=str(OVERSIZED), reason="spam")  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        SoundPlayRequest(channel_id=str(OVERSIZED))  # pyright: ignore[reportCallIssue]


def test_id_lists_are_range_validated():
    with pytest.raises(ValidationError):
        UserReportRequest(  # pyright: ignore[reportCallIssue]
            user_id="123",
            reason="spam",
            evidence_message_ids=[str(OVERSIZED)],
        )
