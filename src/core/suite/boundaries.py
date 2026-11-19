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

import json
import math
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
# A single cell value is copied into the workbook blob, the durable operation
# log, audit payloads and CSV/XLSX exports. Formula text is already bounded by
# ``bound_formula_text`` and the operation's *cell count* is capped, but a
# free-form text value had no ceiling of its own: one operation could persist a
# multi-megabyte string that is reloaded on every read and re-encoded on every
# export (storage/CPU DoS). Keep it generous for legitimate long text yet
# finite, matching the shared "no unbounded workspace field" rule.
MAX_CELL_VALUE_LENGTH = 100_000
# Free-form JSON metadata objects (cell styles, validation criteria and
# conditional-format styles) are serialized into the workbook blob, the
# durable operation-log payload, the live SQLite session and every read/segment
# rebuild. Without a ceiling a single object is a storage/CPU amplification
# vector, and deep nesting makes json.dumps/json.loads recurse dangerously.
# Keep these generous for legitimate formatting but finite.
MAX_WORKSPACE_JSON_SERIALIZED_LENGTH = 16_000
MAX_WORKSPACE_JSON_DEPTH = 12
MAX_WORKSPACE_JSON_CONTAINER_ITEMS = 200
MAX_WORKSPACE_JSON_STRING_LENGTH = 4_000


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


def bound_cell_value(
    value: Any,
    *,
    error_cls: Type[Exception] = HardeningViolation,
) -> Any:
    """Refuse an unbounded single-cell value before it is persisted.

    Non-string values (numbers, booleans, ``None``) are returned untouched;
    only free-form string values can be used as a storage/CPU amplification
    vector. Both the direct operation path and the chunked import path call
    this so neither can bypass the ceiling.
    """
    if isinstance(value, str) and len(value) > MAX_CELL_VALUE_LENGTH:
        raise error_cls(f"Cell value exceeds {MAX_CELL_VALUE_LENGTH} characters")
    return value


def bound_workspace_json(
    value: Any,
    *,
    field: str = "metadata",
    error_cls: Type[Exception] = HardeningViolation,
) -> Any:
    """Refuse an unbounded or pathological free-form JSON object.

    These objects travel the same write choke points as cell values but are
    nested JSON rather than a flat string, so a length ceiling alone is not
    enough: deep nesting can exhaust the recursion limit in ``json.dumps`` and
    a wide container can inflate the serialized payload with a small input.
    Bound the tree (depth, container width, key/string length, finite numbers)
    and its final serialized size. ``None`` is the legitimate "no explicit
    value" case and is passed through untouched.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise error_cls(f"{field} must be an object")

    def walk(node: Any, depth: int) -> None:
        if depth > MAX_WORKSPACE_JSON_DEPTH:
            raise error_cls(f"{field} is nested too deeply")
        if isinstance(node, str):
            if len(node) > MAX_WORKSPACE_JSON_STRING_LENGTH:
                raise error_cls(f"{field} contains a string that is too long")
            try:
                reject_control_characters(node, field)
            except HardeningViolation as exc:
                raise error_cls(str(exc)) from exc
        elif isinstance(node, (dict, list, tuple)):
            if len(node) > MAX_WORKSPACE_JSON_CONTAINER_ITEMS:
                raise error_cls(f"{field} contains too many entries")
            if isinstance(node, dict):
                for key in node:
                    if not isinstance(key, str):
                        raise error_cls(f"{field} keys must be strings")
                    if len(key) > MAX_WORKSPACE_JSON_STRING_LENGTH:
                        raise error_cls(f"{field} contains a key that is too long")
                    try:
                        reject_control_characters(key, f"{field} key")
                    except HardeningViolation as exc:
                        raise error_cls(str(exc)) from exc
            children = node.values() if isinstance(node, dict) else node
            for child in children:
                walk(child, depth + 1)
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise error_cls(f"{field} contains a non-finite number")
        elif node is not None and not isinstance(node, (bool, int)):
            raise error_cls(f"{field} contains an unsupported value")

    walk(value, 0)
    # ``_json`` will serialize this again when persisting; reject an oversized
    # payload here so it never reaches the blob, log or response path.
    encoded = json.dumps(value, separators=(",", ":"))
    if len(encoded) > MAX_WORKSPACE_JSON_SERIALIZED_LENGTH:
        raise error_cls(f"{field} exceeds {MAX_WORKSPACE_JSON_SERIALIZED_LENGTH} bytes")
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
    "MAX_CELL_VALUE_LENGTH",
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
    "bound_cell_value",
    "bound_workspace_json",
    "MAX_WORKSPACE_JSON_SERIALIZED_LENGTH",
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
