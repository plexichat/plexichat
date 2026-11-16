"""Bounds on suite workspace payloads and their user-facing fields.

Suite workspaces (PlexiSum workbooks and PlexiStash items) accept titles,
descriptions, tags and raw file payloads from clients. These helpers give every
suite app one shared definition of "too large" so a new app cannot forget a
check, and they reject control characters that would otherwise be persisted
into titles and audit records.
"""

from __future__ import annotations

from typing import Any, Optional, Type

from src.core.security.hardening import HardeningViolation, reject_control_characters

MAX_WORKSPACE_TITLE_LENGTH = 500
MAX_WORKSPACE_DESCRIPTION_LENGTH = 2000
MAX_WORKSPACE_FIELD_LENGTH = 1000
MAX_WORKSPACE_TAG_LENGTH = 80
MAX_WORKSPACE_TAG_COUNT = 100
MAX_WORKSPACE_PAYLOAD_BYTES = 50 * 1024 * 1024


def bound_workspace_text(
    value: Any,
    field: str,
    *,
    max_length: int = MAX_WORKSPACE_FIELD_LENGTH,
    required: bool = False,
    error_cls: Type[Exception] = HardeningViolation,
) -> Optional[str]:
    """Strip, length-check and control-character-check a workspace field."""
    text = str(value or "").strip()
    if required and not text:
        raise error_cls(f"{field} must not be empty")
    if len(text) > max_length:
        raise error_cls(f"{field} must be at most {max_length} characters")
    reject_control_characters(text, field)
    return text or None


def bound_workspace_payload(
    data: Any,
    *,
    max_bytes: int = MAX_WORKSPACE_PAYLOAD_BYTES,
    error_cls: Type[Exception] = HardeningViolation,
) -> bytes:
    """Return a workspace file payload only if it is a bounded byte string."""
    if not isinstance(data, bytes) or not data:
        raise error_cls("Workspace file payload is required")
    if len(data) > max_bytes:
        raise error_cls(f"Workspace file payload exceeds {max_bytes} bytes")
    return data


def bound_workspace_tags(tags: Any) -> list[str]:
    """De-duplicate, bound and sort a suite workspace tag list."""
    if tags is None:
        return []
    if not isinstance(tags, (list, tuple)):
        raise HardeningViolation("tags must be a list")
    if len(tags) > MAX_WORKSPACE_TAG_COUNT:
        raise HardeningViolation("too many tags")
    cleaned: set[str] = set()
    for tag in tags:
        text = bound_workspace_text(tag, "tag", max_length=MAX_WORKSPACE_TAG_LENGTH)
        if text:
            cleaned.add(text)
    return sorted(cleaned)


__all__ = [
    "MAX_WORKSPACE_DESCRIPTION_LENGTH",
    "MAX_WORKSPACE_FIELD_LENGTH",
    "MAX_WORKSPACE_PAYLOAD_BYTES",
    "MAX_WORKSPACE_TAG_COUNT",
    "MAX_WORKSPACE_TAG_LENGTH",
    "MAX_WORKSPACE_TITLE_LENGTH",
    "bound_workspace_payload",
    "bound_workspace_tags",
    "bound_workspace_text",
]
