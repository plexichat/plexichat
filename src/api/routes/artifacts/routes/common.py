"""Shared helpers for artifact route modules."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status

from ..validation import parse_snowflake_id


def _parse_optional_id(value: Optional[str], field: str) -> Optional[int]:
    """Parse an optional client-supplied ID (compat wrapper)."""
    return parse_snowflake_id(value, field)


def parse_artifact_id(value: str) -> int:
    """Parse a required artifact ID, rejecting anything outside the snowflake range."""
    parsed = parse_snowflake_id(value, "artifact ID")
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
        )
    return parsed
