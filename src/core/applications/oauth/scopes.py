"""
OAuth2 scopes - Scope definitions and validation.
"""

import re
from typing import Any, List, Tuple

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    reject_control_characters,
)

#: A token carries only the handful of scopes an application actually needs.
MAX_SCOPES_PER_REQUEST = 20
MAX_SCOPE_LENGTH = 64
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")

#: Permission bitfields are decimal strings; a 64-bit mask needs at most 20.
MAX_PERMISSION_LENGTH = 20
PERMISSION_MAX = 2**64 - 1


VALID_SCOPES = {
    "identify",
    "email",
    "guilds",
    "guilds.join",
    "guilds.members.read",
    "bot",
    "applications.commands",
    "applications.commands.update",
    "messages.read",
    "webhook.incoming",
}

SCOPE_DESCRIPTIONS = {
    "identify": "Access your username, avatar, and banner",
    "email": "Access your email address",
    "guilds": "Know what servers you are in",
    "guilds.join": "Join servers on your behalf",
    "guilds.members.read": "Read your member info in servers",
    "bot": "Add a bot to a server",
    "applications.commands": "Create commands in servers you manage",
    "applications.commands.update": "Update application commands",
    "messages.read": "Read your messages",
    "webhook.incoming": "Create webhooks in channels you manage",
}

BOT_REQUIRED_SCOPES = {"bot"}

PRIVILEGED_SCOPES = {
    "guilds.members.read",
    "messages.read",
}


def validate_scopes(scopes: List[str]) -> Tuple[bool, List[str]]:
    """
    Validate a list of scopes.

    Args:
        scopes: List of scope strings

    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    if not scopes:
        issues.append("At least one scope is required")
        return False, issues

    if len(scopes) > MAX_SCOPES_PER_REQUEST:
        issues.append(f"At most {MAX_SCOPES_PER_REQUEST} scopes are allowed")

    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        issues.append(f"Invalid scopes: {', '.join(sorted(invalid))}")
    malformed = [
        scope
        for scope in scopes
        if len(scope) > MAX_SCOPE_LENGTH or not SCOPE_PATTERN.fullmatch(scope)
    ]
    if malformed:
        issues.append("Malformed scope strings")

    return len(issues) == 0, issues


def require_valid_scopes(value: Any, *, field: str = "scopes") -> List[str]:
    """Strict form of :func:`validate_scopes` for core/OAuth call sites.

    Returns a de-duplicated list; raises :class:`HardeningViolation` instead of
    returning an issue list so callers cannot accidentally ignore the result.
    """
    if value is None:
        raise HardeningViolation(f"{field} is required")
    try:
        candidates = list(value)
    except TypeError as exc:
        raise HardeningViolation(f"{field} must be a list") from exc
    if not candidates:
        raise HardeningViolation("At least one scope is required")
    if len(candidates) > MAX_SCOPES_PER_REQUEST:
        raise HardeningViolation(
            f"{field} may contain at most {MAX_SCOPES_PER_REQUEST} entries"
        )

    validated: List[str] = []
    for index, candidate in enumerate(candidates):
        label = f"{field}[{index}]"
        scope = reject_control_characters(candidate, label)
        if len(scope) > MAX_SCOPE_LENGTH or not SCOPE_PATTERN.fullmatch(scope):
            raise HardeningViolation(f"{label} is not a valid scope string")
        if scope not in VALID_SCOPES:
            raise HardeningViolation(f"{label} is not a known scope: {scope}")
        if scope not in validated:
            validated.append(scope)
    return validated


def require_allowed_scopes(value: Any, *, field: str = "allowed_scopes") -> List[str]:
    """Validate an application's granted-scope ceiling.

    Applications may only ever request/record a subset of the globally valid
    scopes. An empty or legacy value is treated as "the full default set" by
    the caller (see ``OAuth2Flow._application_allowed_scopes``); an explicit
    allow-list is validated with the same strict rules as a request.
    """
    validated = require_valid_scopes(value, field=field)
    if len(validated) > MAX_SCOPES_PER_REQUEST:
        raise HardeningViolation(
            f"{field} may contain at most {MAX_SCOPES_PER_REQUEST} entries"
        )
    return validated


def scopes_are_subset(requested: List[str], allowed: List[str]) -> bool:
    """Return True when every requested scope is present in ``allowed``."""
    allowed_set = set(allowed)
    return all(scope in allowed_set for scope in requested)


def validate_permission_string(value: Any, *, field: str = "permissions") -> str:
    """Return a canonical decimal permission bitfield of bounded size."""
    if value is None:
        return "0"
    text = reject_control_characters(value, field).strip()
    if not text or len(text) > MAX_PERMISSION_LENGTH or not text.isdigit():
        raise HardeningViolation(f"{field} must be a decimal permission bitfield")
    return str(bounded_int(text, field, minimum=0, maximum=PERMISSION_MAX))


def parse_scopes(scope_string: str) -> List[str]:
    """
    Parse a space-separated scope string.

    Args:
        scope_string: Space-separated scopes

    Returns:
        List of scope strings
    """
    if not scope_string:
        return []
    return [s.strip() for s in scope_string.split() if s.strip()]


def scopes_to_string(scopes: List[str]) -> str:
    """
    Convert a list of scopes to a space-separated string.

    Args:
        scopes: List of scope strings

    Returns:
        Space-separated scope string
    """
    return " ".join(sorted(scopes))


def has_scope(granted_scopes: List[str], required_scope: str) -> bool:
    """
    Check if a required scope is in the granted scopes.

    Args:
        granted_scopes: List of granted scopes
        required_scope: Scope to check for

    Returns:
        True if scope is granted
    """
    return required_scope in granted_scopes


def get_scope_description(scope: str) -> str:
    """
    Get the human-readable description for a scope.

    Args:
        scope: Scope string

    Returns:
        Description string
    """
    return SCOPE_DESCRIPTIONS.get(scope, scope)
