"""Fail-closed guards for the DSAR export generation path.

Three properties are enforced here, independently of the calling route:

* **Identifier binding** — an export id is a bounded positive snowflake. A
  looked-up row must be the row that was asked for, so a helper that returns a
  different record can never satisfy a client supplied id (IDOR defence).
* **Filename safety** — the storage object name is assembled only from a
  validated numeric id and an allowlisted extension. No caller supplied text
  can introduce a path separator, traversal component or header/control byte.
* **Row bounds** — a category payload larger than a hard cap is truncated and
  marked incomplete so the existing quota/completeness gate fails closed
  instead of serialising an effectively unbounded artifact.

These helpers are dependency-free so the manager, generator and routes can all
share the same checks.
"""

from __future__ import annotations

from typing import Any, Dict

from src.core.security.hardening import HardeningViolation, parse_id

MAX_SNOWFLAKE_ID = 2**63 - 1

# Hard ceiling on rows returned per collector category. Configured export
# quotas may be lower; this is the absolute backstop that bounds the bytes a
# single runaway collector query can pull into memory on the hot path.
MAX_EXPORT_ROWS_PER_CATEGORY = 100_000

_ALLOWED_EXPORT_FORMATS = frozenset({"json", "zip"})


class InvalidExportInput(ValueError):
    """Raised when an export identifier or filename cannot be trusted."""


def validate_export_id(value: Any, label: str = "request id") -> int:
    """Return ``value`` as a bounded positive snowflake or fail closed.

    Parsing goes through the shared ASCII-only snowflake gate, never a bare
    ``int()``. ``int()`` accepts Unicode decimal homoglyphs (``"١٢٣"``,
    ``"１２３"``) and PEP 515 separators (``"1_0"``) that every other id
    boundary on the request path (``SnowflakeIDParam``, ``parse_id``) rejects.
    Accepting them here is a parser differential: the value this helper
    normalises for the storage/object name can differ from the raw string a
    caller carries into a subsequent lookup or audit record. Routing through
    :func:`parse_id` keeps validation and consumption on the same
    interpretation of the token; non-ASCII or separator-bearing forms are
    refused.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        # A float would be lossy for 64-bit snowflakes; reject any other
        # type outright rather than coercing it into a valid-looking id.
        raise InvalidExportInput(f"Invalid {label}")
    try:
        return parse_id(value, label)
    except (HardeningViolation, ValueError, TypeError, OverflowError) as exc:
        raise InvalidExportInput(f"Invalid {label}") from exc


def safe_export_filename(request_id: Any, export_format: Any) -> str:
    """Build a storage object name from a validated id and allowlisted format.

    The result contains only ASCII digits, an underscore, and a known
    extension — it cannot carry ``/``, ``..``, quotes, CR/LF or any other
    character that could steer a filesystem path or a download header.
    """
    rid = validate_export_id(request_id, "request id")
    fmt = str(export_format or "").strip().lower()
    if fmt not in _ALLOWED_EXPORT_FORMATS:
        raise InvalidExportInput("Unsupported export format")
    return f"dsar_{rid}.{fmt}"


# Envelope keys are metadata, not row categories, so they are never truncated.
_ENVELOPE_KEYS = frozenset(
    {"exported_at", "user_id", "export_version", "export_metadata"}
)


def bound_export_data(
    data: Dict[str, Any], max_rows: int = MAX_EXPORT_ROWS_PER_CATEGORY
) -> bool:
    """Cap every row list in ``data`` at ``max_rows``.

    Returns ``True`` when anything was truncated. On truncation the payload is
    marked ``complete=False`` with a warning so ``assert_export_ready`` refuses
    to write an artifact: a partial DSAR export is worse than no export.
    """
    if max_rows < 1:
        raise ValueError("max_rows must be positive")

    truncated = False

    def _cap(value: Any) -> Any:
        nonlocal truncated
        if isinstance(value, list):
            if len(value) > max_rows:
                truncated = True
                return value[:max_rows]
            return value
        if isinstance(value, dict):
            for key, child in list(value.items()):
                value[key] = _cap(child)
        return value

    for key, value in list(data.items()):
        if key in _ENVELOPE_KEYS:
            continue
        capped = _cap(value)
        if capped is not value:
            data[key] = capped

    if truncated:
        metadata = data.get("export_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            data["export_metadata"] = metadata
        metadata["complete"] = False
        warnings = metadata.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
            metadata["warnings"] = warnings
        warnings.append(
            f"export category exceeded the hard {max_rows}-row cap; artifact withheld"
        )

    return truncated


__all__ = [
    "MAX_SNOWFLAKE_ID",
    "MAX_EXPORT_ROWS_PER_CATEGORY",
    "InvalidExportInput",
    "validate_export_id",
    "safe_export_filename",
    "bound_export_data",
]
