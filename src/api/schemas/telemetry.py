from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Union


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
        ..., ge=0, description="The response time in milliseconds"
    )
    status_code: Union[int, str] = Field(
        ..., description="The HTTP status code returned"
    )
    db_queries: Optional[int] = Field(
        0, description="Number of database queries executed"
    )
    db_time_ms: Optional[float] = Field(
        0.0, description="Time spent executing database queries in ms"
    )
    timestamp: Optional[int] = Field(
        None,
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
