"""Boundary validation for values owned by the applications module.

Every helper raises :class:`HardeningViolation` (a ``ValueError`` subclass) so
existing ``except ValueError`` handlers at the API boundary keep working. The
actual rules live in ``src.core.security.hardening``; this module only composes
them into the shapes the applications domain accepts and adds the domain's own
caps (counts, lengths and scheme allow-lists).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional
from urllib.parse import urlparse

from src.core.security.hardening import (
    HardeningViolation,
    reject_control_characters,
    require_http_url,
    require_https_url,
)

#: OAuth callback allow-lists are small; anything larger is an enumeration risk.
MAX_REDIRECT_URIS = 20
MAX_REDIRECT_URI_LENGTH = 2048

#: Plain HTTP is only meaningful for a local developer callback. Everything
#: else must be HTTPS; the set is explicit so it can be reviewed and tested.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Display text. These are the same ceilings the HTTP schema declares; keeping
# them here means a core caller cannot bypass them by skipping the route.
MIN_APP_NAME_LENGTH = 2
MAX_APP_NAME_LENGTH = 100
MAX_APP_DESCRIPTION_LENGTH = 2000
MAX_APP_SHORT_DESCRIPTION_LENGTH = 256
MAX_HOMEPAGE_URL_LENGTH = 2048
MAX_APPLICATION_FILTER_LENGTH = 128

# Webhook/interaction endpoints are server-to-server targets. They must be
# HTTPS, and the configured redirect pattern must be a compilable, bounded regex.
MAX_WEBHOOK_URL_LENGTH = 2048
MAX_URL_PATTERN_LENGTH = 512

# Bot directory free-form data is persisted and echoed to every directory
# reader. Without ceilings a single application owner could turn the directory
# into an unbounded payload (storage bloat + response amplification), so caps
# are enforced here at the core boundary, not just in the HTTP schema.
MAX_BOT_TAGS = 25
MAX_BOT_TAG_LENGTH = 32
MAX_BOT_NAME_LENGTH = 100
MAX_BOT_REASON_LENGTH = 2000

#: A registered redirect URI is a literal callback address, never a pattern.
#: Wildcard/regex metacharacters would turn the allow-list into an open
#: redirector, so they are rejected both when storing and when matching.
_REDIRECT_PATTERN_CHARS = frozenset("*[]{}()|\\^$+")


def _contains_redirect_pattern(value: str) -> bool:
    return any(char in value for char in _REDIRECT_PATTERN_CHARS)


def require_exact_redirect_match(
    candidate: Any, allowed_uris: Any, *, field: str = "redirect URI"
) -> str:
    """Return ``candidate`` only when it literally equals a registered URI.

    The comparison is an exact string equality: no trailing-slash folding, no
    case folding, no substring/prefix matching and no wildcard expansion. The
    candidate is validated first so malformed or unsafe URLs never reach the
    equality check. Pattern-like registered entries are ignored rather than
    treated as wildcards.
    """
    text = reject_control_characters(candidate, field).strip()
    if not text or len(text) > MAX_REDIRECT_URI_LENGTH:
        raise HardeningViolation(f"{field} has an invalid length")
    canonical = require_oauth_redirect_url(text, field=field)
    if canonical != text:
        raise HardeningViolation(f"{field} must be an exact registered URI")
    if _contains_redirect_pattern(text):
        raise HardeningViolation(f"{field} must not contain wildcard characters")

    try:
        entries = list(allowed_uris or [])
    except TypeError as exc:
        raise HardeningViolation(f"{field} allow-list is invalid") from exc
    if len(entries) > MAX_REDIRECT_URIS:
        raise HardeningViolation(f"{field} allow-list is too large")

    for entry in entries:
        if not isinstance(entry, str) or _contains_redirect_pattern(entry):
            continue
        if entry == text:
            return text
    raise HardeningViolation(f"{field} is not registered")


def require_oauth_redirect_url(value: Any, *, field: str = "redirect URI") -> str:
    """Return an absolute, credential-free OAuth callback URL.

    HTTPS is required in general. HTTP is allow-listed only for loopback hosts,
    which cannot be used as a public redirect target and are needed by native
    desktop flows during development.
    """
    raw = reject_control_characters(value, field).strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() == "http" and host in LOOPBACK_HOSTS:
        url = require_http_url(raw, field)
    else:
        url = require_https_url(raw, field)
    if urlparse(url).fragment:
        raise HardeningViolation(f"{field} must not contain a fragment")
    return url


def validate_redirect_uris(value: Any, *, field: str = "redirect_uris") -> List[str]:
    """Validate and normalise an OAuth redirect-URI allow-list."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise HardeningViolation(f"{field} must be a list of URIs")
    try:
        candidates: List[Any] = list(value)
    except TypeError as exc:
        raise HardeningViolation(f"{field} must be a list of URIs") from exc

    if len(candidates) > MAX_REDIRECT_URIS:
        raise HardeningViolation(
            f"{field} may contain at most {MAX_REDIRECT_URIS} entries"
        )

    validated: List[str] = []
    for index, candidate in enumerate(candidates):
        label = f"{field}[{index}]"
        text = reject_control_characters(candidate, label)
        if len(text) > MAX_REDIRECT_URI_LENGTH:
            raise HardeningViolation(
                f"{label} exceeds {MAX_REDIRECT_URI_LENGTH} characters"
            )
        url = require_oauth_redirect_url(text, field=label)
        if _contains_redirect_pattern(url):
            raise HardeningViolation(
                f"{label} must not contain wildcard or pattern characters"
            )
        # Exact-match allow-lists do not benefit from duplicates; dropping them
        # keeps the stored list canonical and bounded.
        if url not in validated:
            validated.append(url)
    return validated


def validate_application_name(value: Any) -> str:
    """Return a normalised application name of a displayable length."""
    name = reject_control_characters(value, "name").strip()
    if len(name) < MIN_APP_NAME_LENGTH or len(name) > MAX_APP_NAME_LENGTH:
        raise HardeningViolation(
            f"name must be between {MIN_APP_NAME_LENGTH} and "
            f"{MAX_APP_NAME_LENGTH} characters"
        )
    return name


def validate_application_description(
    value: Any,
    *,
    field: str = "description",
    max_length: int = MAX_APP_DESCRIPTION_LENGTH,
) -> Optional[str]:
    """Bound free-form application/bot description text."""
    if value is None:
        return None
    text = reject_control_characters(value, field)
    if len(text) > max_length:
        raise HardeningViolation(f"{field} exceeds {max_length} characters")
    return text


def validate_homepage_url(value: Any, *, field: str = "url") -> Optional[str]:
    """Validate an informational link (site, terms, privacy, support, ...).

    Unlike redirect URIs these are informational and may be plain HTTP, but
    they must still be absolute, credential-free and of a bounded length.
    """
    if value is None:
        return None
    text = reject_control_characters(value, field).strip()
    if len(text) > MAX_HOMEPAGE_URL_LENGTH:
        raise HardeningViolation(
            f"{field} exceeds {MAX_HOMEPAGE_URL_LENGTH} characters"
        )
    return require_http_url(text, field)


def validate_webhook_url(value: Any, *, field: str = "webhook_url") -> Optional[str]:
    """Constrain a server-to-server callback target to absolute HTTPS."""
    if value is None:
        return None
    text = reject_control_characters(value, field).strip()
    if len(text) > MAX_WEBHOOK_URL_LENGTH:
        raise HardeningViolation(f"{field} exceeds {MAX_WEBHOOK_URL_LENGTH} characters")
    url = require_https_url(text, field)
    if urlparse(url).fragment:
        raise HardeningViolation(f"{field} must not contain a fragment")
    return url


def validate_config_url_pattern(
    value: Any, *, field: str = "allowed_redirect_uri_pattern"
) -> Optional[str]:
    """Validate the configured redirect-URI regex before it is compiled/used."""
    if value is None:
        return None
    text = reject_control_characters(value, field)
    if len(text) > MAX_URL_PATTERN_LENGTH:
        raise HardeningViolation(f"{field} exceeds {MAX_URL_PATTERN_LENGTH} characters")
    try:
        re.compile(text)
    except re.error as exc:
        raise HardeningViolation(f"{field} is not a valid regular expression") from exc
    return text


def validate_bot_tags(value: Any, *, field: str = "tags") -> List[str]:
    """Validate and bound a bot profile's tag list.

    Returns a de-duplicated list of non-empty, control-character-free tags.
    Both the number of tags and each tag's length are capped so a profile
    cannot be used as an unbounded data carrier in the public directory.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise HardeningViolation(f"{field} must be a list of tags")
    try:
        candidates: List[Any] = list(value)
    except TypeError as exc:
        raise HardeningViolation(f"{field} must be a list of tags") from exc
    if len(candidates) > MAX_BOT_TAGS:
        raise HardeningViolation(f"{field} may contain at most {MAX_BOT_TAGS} entries")
    tags: List[str] = []
    for index, candidate in enumerate(candidates):
        label = f"{field}[{index}]"
        text = reject_control_characters(candidate, label).strip()
        if not text or len(text) > MAX_BOT_TAG_LENGTH:
            raise HardeningViolation(f"{label} has an invalid length")
        # Exact-match tags gain nothing from duplicates; keep the list canonical.
        if text not in tags:
            tags.append(text)
    return tags


def validate_bot_display_name(value: Any, *, field: str = "bot_name") -> Optional[str]:
    """Bound a bot's display name, which is persisted and shown publicly."""
    if value is None:
        return None
    text = reject_control_characters(value, field).strip()
    if not text or len(text) > MAX_BOT_NAME_LENGTH:
        raise HardeningViolation(f"{field} has an invalid length")
    return text


def validate_bot_reason(value: Any, *, field: str = "reason") -> Optional[str]:
    """Bound the free-form reason attached to bot requests and reviews."""
    if value is None:
        return None
    text = reject_control_characters(value, field)
    if len(text) > MAX_BOT_REASON_LENGTH:
        raise HardeningViolation(f"{field} exceeds {MAX_BOT_REASON_LENGTH} characters")
    return text
