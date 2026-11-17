"""Boundary guard for self-test configuration.

The ``selftest`` section of the config is operator/admin-controlled. Its values
are threaded into request paths, request bodies, a persistent journal path, and
worker counts. An earlier release passed them through verbatim, so a tampered
config could plant a template/script payload or point the journal writer at an
arbitrary filesystem location.

This module normalizes that config once, at the runner boundary:

* string values are length-bounded and screened for active content
  (script/template delimiters, code-execution names, control characters);
* list values are capped and every member is screened the same way;
* ``parallel_get_workers`` is confined to a small integer range;
* ``journal_path`` must resolve inside a known data directory or it falls back
  to the shipped default.

No value that crosses this boundary is ever interpreted as code or markup.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict

_MAX_STRING_CHARS = 512
_MAX_LIST_ENTRIES = 256
_MAX_DICT_ENTRIES = 128
_MAX_NESTING = 6

# Patterns that turn a benign-looking config string into an active payload
# when it is later echoed into HTML, a template, a shell, or an eval context.
_ACTIVE_CONTENT_RE = re.compile(
    r"<\s*/?\s*script\b"
    r"|<\s*/?\s*iframe\b"
    r"|javascript\s*:"
    r"|\bon(error|load|click|focus|mouseover)\s*="
    r"|\{\{|\{%|%\}|\}\}"
    r"|\b__import__\b|\bos\.system\b|\bsubprocess\b"
    r"|\beval\s*\(|\bexec\s*\(|\bcompile\s*\(|\bpickle\b"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
    re.IGNORECASE,
)

_DEFAULT_JOURNAL_PATH = os.path.join("data", "selftest_artifact_journal.json")

_ROUTE_VALUE_RE = re.compile(r"[A-Za-z0-9_:/{}.?=&%+-]{1,200}")


class ConfigRejected(ValueError):
    """Raised when a config value cannot be safely normalized."""


def _screen_text(value: str, label: str) -> str:
    """Reject active content and bound a config string."""
    if not isinstance(value, str):
        raise ConfigRejected(f"{label} must be a string")
    if len(value) > _MAX_STRING_CHARS:
        raise ConfigRejected(f"{label} exceeds {_MAX_STRING_CHARS} characters")
    if _ACTIVE_CONTENT_RE.search(value):
        raise ConfigRejected(f"{label} contains active content")
    return value


def _safe_journal_path(value: Any) -> str:
    """Confine the journal writer to a known data directory."""
    if not isinstance(value, str) or not value:
        return _DEFAULT_JOURNAL_PATH
    try:
        candidate = _screen_text(value, "journal_path")
    except ConfigRejected:
        return _DEFAULT_JOURNAL_PATH

    # Reject filesystem escape attempts before resolution as well as after.
    if os.path.isabs(candidate) and ".." in Path(candidate).parts:
        return _DEFAULT_JOURNAL_PATH
    if "\x00" in candidate:
        return _DEFAULT_JOURNAL_PATH

    allowed_roots = [
        (Path.home() / ".plexichat" / "data").resolve(),
        (Path(__file__).resolve().parents[3] / "data").resolve(),
    ]
    try:
        resolved = Path(candidate).expanduser().resolve()
        if any(resolved.is_relative_to(root) for root in allowed_roots):
            return str(resolved)
    except (OSError, ValueError):
        pass
    return _DEFAULT_JOURNAL_PATH


def sanitize_selftest_config(config: Any) -> Dict[str, Any]:
    """Return a bounded, active-content-free copy of the selftest config.

    Invalid values are dropped (or, for ``journal_path``, replaced by the
    shipped default) instead of aborting the whole run: a hostile config must
    never become a code-execution or arbitrary-write primitive, but it should
    also not be able to crash the operator's harness by itself.
    """
    if not isinstance(config, dict):
        return {}

    def _clean(value: Any, label: str, depth: int) -> Any:
        if depth > _MAX_NESTING:
            raise ConfigRejected(f"{label} nested too deeply")
        if isinstance(value, str):
            return _screen_text(value, label)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            if abs(value) > 10**9:
                raise ConfigRejected(f"{label} integer out of range")
            return value
        if isinstance(value, float):
            if not (0 <= value <= 10**6):
                raise ConfigRejected(f"{label} float out of range")
            return value
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            if len(value) > _MAX_LIST_ENTRIES:
                raise ConfigRejected(f"{label} has too many entries")
            items = []
            for i, item in enumerate(value):
                try:
                    items.append(_clean(item, f"{label}[{i}]", depth + 1))
                except ConfigRejected:
                    # Skip only the hostile member, not the whole config field.
                    continue
            return items
        if isinstance(value, dict):
            if len(value) > _MAX_DICT_ENTRIES:
                raise ConfigRejected(f"{label} has too many keys")
            result: Dict[str, Any] = {}
            for key, item in value.items():
                safe_key = _screen_text(key, f"{label} key")
                result[safe_key] = _clean(item, f"{label}.{safe_key}", depth + 1)
            return result
        raise ConfigRejected(f"{label} has an unsupported type")

    cleaned: Dict[str, Any] = {}
    for key, value in config.items():
        try:
            safe_key = _screen_text(key, "config key")
            cleaned[safe_key] = _clean(value, safe_key, 0)
        except ConfigRejected:
            # Drop the offending value; never let it reach a sink.
            continue

    # Route exclusions are paths: enforce a route-shaped allowlist too.
    excluded = cleaned.get("excluded_endpoints")
    if isinstance(excluded, list):
        cleaned["excluded_endpoints"] = [
            entry
            for entry in excluded
            if isinstance(entry, str)
            and _ROUTE_VALUE_RE.fullmatch(entry)
            and ".." not in Path(entry).parts
            and not entry.startswith("//")
        ]
    elif excluded is not None:
        cleaned.pop("excluded_endpoints", None)

    workers = cleaned.get("parallel_get_workers")
    if workers is not None:
        if (
            not isinstance(workers, int)
            or isinstance(workers, bool)
            or not 1 <= workers <= 64
        ):
            cleaned.pop("parallel_get_workers", None)

    # The journal path is the one config value that becomes a filesystem
    # write target, so it gets an additional confinement check.
    cleaned["journal_path"] = _safe_journal_path(config.get("journal_path"))
    return cleaned
