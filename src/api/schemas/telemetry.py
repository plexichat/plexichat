from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional

class ResponseTimeEntry(BaseModel):
    """A single response time measurement."""
    endpoint: str = Field(..., max_length=255, description="The endpoint being measured")
    method: str = Field(..., max_length=10, description="The HTTP method used")
    response_time_ms: float = Field(..., ge=0, description="The response time in milliseconds")
    status_code: int = Field(..., ge=100, le=599, description="The HTTP status code returned")
    timestamp: Optional[int] = Field(None, description="The Unix timestamp of the measurement")

class TelemetrySubmission(BaseModel):
    """Batch submission of response time data."""
    entries: List[ResponseTimeEntry] = Field(..., max_length=100, description="The list of measurements to submit")

class TelemetryResponse(BaseModel):
    """Response for telemetry submission."""
    model_config = ConfigDict(from_attributes=True)

    accepted: int = Field(..., description="The number of entries accepted")
    message: str = Field(..., description="A status message")
