"""Bounds on suite workspace payloads and their user-facing fields.

Suite workspaces (PlexiSum workbooks and PlexiStash items) accept titles,
descriptions, tags and raw file payloads from clients. These helpers give every
suite app one shared definition of "too large" so a new app cannot forget a
check, and they reject control characters that would otherwise be persisted
into titles and audit records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Type

import re

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

MAX_WORKSPACE_TITLE_LENGTH = 500
MAX_WORKSPACE_DESCRIPTION_LENGTH = 2000
MAX_WORKSPACE_FIELD_LENGTH = 1000
MAX_WORKSPACE_TAG_LENGTH = 80
MAX_WORKSPACE_TAG_COUNT = 100
MAX_WORKSPACE_PAYLOAD_BYTES = 50 * 1024 * 1024
# Imports and formulas are parsed recursively; bound both the source text
# and its nesting before the parser is allowed to run.
MAX_FORMULA_LENGTH = 8192
MAX_FORMULA_NESTING_DEPTH = 128
MAX_CSV_IMPORT_BYTES = 2_000_000
MAX_XLSX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_IMPORT_CELLS = 1_000_000


# The same catalog the registry serves. Kept here as an explicit allowlist so
# an app id or artifact id from a request is never used to import a module,
# open a path or construct a type name.
SUITE_APP_IDS = frozenset(
    {
        "plexichat",
        "plexiscribe",
        "plexiscript",
        "plexiboard",
        "plexistash",
        "plexisum",
    }
)
SUITE_ARTIFACT_TYPES = frozenset(
    {"plexiscribe", "plexiscript", "whiteboard", "stash", "sum"}
)
SUITE_CAPABILITIES = frozenset(
    {
        "plexiscribe",
        "plexiscript",
        "artifacts_whiteboard",
        "plexistash",
        "plexisum",
    }
)
_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


def validate_app_id(value: Any) -> str:
    """Return a suite app id only if it is on the server-owned allowlist."""
    if (
        not isinstance(value, str)
        or not _APP_ID_RE.match(value)
        or value not in SUITE_APP_IDS
    ):
        raise HardeningViolation("Unknown suite app id")
    return value


def validate_artifact_type(value: Any) -> str:
    """Return an artifact type only if the suite declares it."""
    if not isinstance(value, str) or value not in SUITE_ARTIFACT_TYPES:
        raise HardeningViolation("Unknown suite artifact type")
    return value


def validate_artifact_id(value: Any) -> int:
    """Parse an artifact id as a real positive snowflake."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise HardeningViolation("artifact_id must be an integer id")
    return parse_id(value, "artifact_id")


@dataclass(frozen=True)
class ConfigRule:
    """One declared shared-config key: its kind, bounds and safe default."""

    kind: str
    default: Any
    minimum: int = 0
    maximum: int = 0
    allowed: frozenset = frozenset()


# Keys the suite is willing to read from ``artifacts.plexistash`` (and any
# other suite section using this helper). Anything not declared here is
# dropped: an unknown key must never change behaviour or be interpolated into
# a query or path.
SHARED_CONFIG_SCHEMA = {
    "cleanup_interval_minutes": ConfigRule("int", 60, minimum=1, maximum=1440),
    "retention_days": ConfigRule("int", 30, minimum=0, maximum=36500),
    "ticket_ttl_minutes": ConfigRule("int", 30, minimum=1, maximum=1440),
    "max_items_per_page": ConfigRule("int", 50, minimum=1, maximum=200),
    "workspace_theme": ConfigRule(
        "enum", "system", allowed=frozenset({"light", "dark", "system"})
    ),
}


def validate_shared_config(
    config: Any,
    *,
    section: Optional[str] = None,
) -> Dict[str, Any]:
    """Return only declared shared-config keys with schema-bounded values.

    Unknown keys are discarded and out-of-range or wrongly typed values fall
    back to their declared default, so a malformed deployment config cannot
    drive huge cleanup intervals, negative retention windows or arbitrary
    enum values into the suite.
    """
    defaults = {key: rule.default for key, rule in SHARED_CONFIG_SCHEMA.items()}
    if not isinstance(config, dict):
        return defaults
    raw: Any = config.get(section, {}) if section else config
    if not isinstance(raw, dict):
        return defaults

    result: Dict[str, Any] = {}
    for key, rule in SHARED_CONFIG_SCHEMA.items():
        if key not in raw:
            result[key] = rule.default
            continue
        value = raw[key]
        if rule.kind == "int":
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                result[key] = rule.default
                continue
            try:
                result[key] = bounded_int(
                    value,
                    f"shared_config.{key}",
                    minimum=rule.minimum,
                    maximum=rule.maximum,
                    default=rule.default,
                )
            except HardeningViolation:
                result[key] = rule.default
        elif value in rule.allowed:
            result[key] = value
        else:
            result[key] = rule.default
    return result


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


def bound_formula_text(
    value: Any,
    *,
    error_cls: Type[Exception] = HardeningViolation,
) -> str:
    """Bound formula source before it reaches the recursive parser.

    The parser recurses once per parenthesised group, so a few kilobytes of
    nested parentheses could otherwise exhaust the Python stack. A formula is
    also copied into stored cells and audit records, so control characters are
    refused here.
    """
    if not isinstance(value, str):
        raise error_cls("Formula must be a string")
    if len(value) > MAX_FORMULA_LENGTH:
        raise error_cls(f"Formula exceeds {MAX_FORMULA_LENGTH} characters")
    if value.count("(") > MAX_FORMULA_NESTING_DEPTH:
        raise error_cls("Formula nesting is too deep")
    reject_control_characters(value, "formula")
    return value


def bound_import_bytes(
    value: Any,
    *,
    max_bytes: int,
    error_cls: Type[Exception] = HardeningViolation,
) -> bytes:
    """Bound an already-decoded workbook import before parsing it."""
    if not isinstance(value, bytes):
        raise error_cls("Import payload must be bytes")
    if len(value) > max_bytes:
        raise error_cls(f"Import payload exceeds {max_bytes} bytes")
    return value


def bound_import_text(
    value: Any,
    *,
    max_bytes: int,
    error_cls: Type[Exception] = HardeningViolation,
) -> str:
    """Bound a text import by its encoded size."""
    if not isinstance(value, str):
        raise error_cls("Import payload must be text")
    if len(value.encode("utf-8")) > max_bytes:
        raise error_cls(f"Import payload exceeds {max_bytes} bytes")
    return value


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
    "MAX_CSV_IMPORT_BYTES",
    "MAX_FORMULA_LENGTH",
    "MAX_FORMULA_NESTING_DEPTH",
    "MAX_IMPORT_CELLS",
    "MAX_WORKSPACE_DESCRIPTION_LENGTH",
    "MAX_XLSX_IMPORT_BYTES",
    "MAX_WORKSPACE_FIELD_LENGTH",
    "MAX_WORKSPACE_PAYLOAD_BYTES",
    "MAX_WORKSPACE_TAG_COUNT",
    "MAX_WORKSPACE_TAG_LENGTH",
    "MAX_WORKSPACE_TITLE_LENGTH",
    "SHARED_CONFIG_SCHEMA",
    "SUITE_APP_IDS",
    "SUITE_ARTIFACT_TYPES",
    "SUITE_CAPABILITIES",
    "bound_formula_text",
    "bound_import_bytes",
    "bound_import_text",
    "bound_workspace_payload",
    "validate_shared_config",
    "bound_workspace_tags",
    "bound_workspace_text",
    "validate_app_id",
    "validate_artifact_id",
    "validate_artifact_type",
]
