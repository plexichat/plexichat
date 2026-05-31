"""
Log schemas.
"""

from typing import List
from pydantic import BaseModel, ConfigDict


class LogFileInfo(BaseModel):
    """Metadata for a log file."""

    filename: str
    size: int
    modified: int
    is_zipped: bool


class LogLine(BaseModel):
    """A single log entry."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: str
    level: str
    message: str
    raw: str


class LogViewResponse(BaseModel):
    """Log file content with pagination."""

    filename: str
    total_lines: int
    lines: List[LogLine]
    limit: int
    offset: int
