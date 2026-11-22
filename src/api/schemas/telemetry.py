from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Union

from src.core.security.hardening import SNOWFLAKE_MAX


# These mirror the caps the telemetry core applies before persisting a sample.
# Enforcing them here -- at the unauthenticated request boundary -- means a
# hostile flush is rejected as a validation error instead of being accepted,
# silently dropped per-entry, and (in the core's float comparisons) partly
# stored.  The float caps are the core's 24-hour metric window in milliseconds.
_MAX_RESPONSE_TIME_MS = 24 * 60 * 60 * 1000
_MAX_DB_TIME_MS = 24 * 60 * 60 * 1000
_MAX_DB_QUERIES = 1_000_000

# ``allow_inf_nan=False`` is load-bearing: pydantic accepts ``NaN``/``Infinity``
# (as JSON literals or strings) by default.  NaN defeats every range comparison
# in the core (``x < 0`` and ``x > max`` are both False), so it reached the
# ``REAL`` columns and turned later ``AVG()`` aggregates into NaN.  +Infinity
# likewise overflowed stored metrics.  Reject both on every float field, and
# keep the ``ge=0`` checks so legitimate negative values are still refused.
# The constraints are spelled out per field: spreading a shared ``dict`` into
# ``Field(**constraints)`` hides each keyword from the typed overload, so the
# same guard read as a type error instead of a checked constraint.


class ResponseTimeEntry(BaseModel):
    """A single response time measurement."""

    # M1: unknown fields used to be silently dropped (pydantic's default
    # ``ignore``), so a client typo like ``usr_id`` returned 200 while the
    # entry was stored empty. Forbid extras so contract drift surfaces as a
    # 422 instead of silent data loss.
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(
        ..., max_length=255, description="The endpoint being measured"
    )
    method: str = Field(..., max_length=10, description="The HTTP method used")
    response_time_ms: float = Field(
        ...,
        ge=0,
        le=_MAX_RESPONSE_TIME_MS,
        allow_inf_nan=False,
        description="The response time in milliseconds",
    )
    status_code: Union[int, str] = Field(
        ..., description="The HTTP status code returned"
    )
    db_queries: Optional[int] = Field(
        0,
        ge=0,
        le=_MAX_DB_QUERIES,
        description="Number of database queries executed",
    )
    db_time_ms: Optional[float] = Field(
        0.0,
        ge=0,
        le=_MAX_DB_TIME_MS,
        allow_inf_nan=False,
        description="Time spent executing database queries in ms",
    )
    timestamp: Optional[int] = Field(
        None,
        ge=0,
        le=SNOWFLAKE_MAX,
        description="The Unix timestamp of the measurement; omitted/None defaults to server-receive time",
    )


class TelemetrySubmission(BaseModel):
    """Batch submission of response time data."""

    # M1: forbid unknown top-level keys too (see ResponseTimeEntry).
    model_config = ConfigDict(extra="forbid")

    entries: List[ResponseTimeEntry] = Field(
        ..., max_length=100, description="The list of measurements to submit"
    )


class TelemetryResponse(BaseModel):
    """Response for telemetry submission."""

    model_config = ConfigDict(from_attributes=True)

    accepted: int = Field(..., description="The number of entries accepted")
    message: str = Field(..., description="A status message")
