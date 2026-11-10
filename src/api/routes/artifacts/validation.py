"""Validation and safety helpers for artifact routes."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException, status

from .constants import (
    MAX_FILENAME_LEN,
    MAX_ID_LEN,
    SAFE_FILENAME_RE,
    VALID_EXPORT_FORMATS,
)


def parse_snowflake_id(value: Optional[str], field: str) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > MAX_ID_LEN or not text.isdecimal():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        )
    try:
        parsed = int(text)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        ) from None
    if parsed <= 0 or parsed > (2**63 - 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        )
    return parsed


def validate_export_format(export_format: str) -> str:
    fmt = str(export_format or "").strip().lower()
    if fmt not in VALID_EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Unsupported export format"}},
        )
    return fmt


def safe_download_filename(title: Optional[str], export_format: str) -> tuple[str, str]:
    fmt = validate_export_format(export_format)
    base = (title or "export").strip().lower().replace(" ", "-")
    base = SAFE_FILENAME_RE.sub("-", base).strip("-._") or "export"
    base = base[:MAX_FILENAME_LEN]
    filename = f"{base}.{fmt}"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or f"export.{fmt}"
    ascii_name = re.sub(r'[\r\n"\\]', "", ascii_name)
    return ascii_name, quote(filename, safe="")
