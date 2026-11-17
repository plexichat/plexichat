"""Recursive redaction of secrets in anything that gets logged.

Audit details, admin log lines and exported rows are free-form data supplied
by callers. A token pasted into ``details`` must not survive into a log viewer
or a CSV export. :func:`redact_secrets` walks nested mappings/sequences, drops
values under sensitive keys, and scrubs secret-looking substrings out of free
text. It is bounded in depth, item count and string length so hostile input
cannot turn redaction into a CPU/memory sink.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

REDACTED = "[REDACTED]"

_MAX_DEPTH = 12
_MAX_ITEMS = 4096
_MAX_CHARS = 64 * 1024
_MAX_STRING_CHARS = 4096

# Tokenized matching avoids redacting harmless keys such as ``author`` or
# ``monkey`` while still catching ``api_key``, ``apiKey``, ``access-token``,
# ``user_password`` and ``clientSecret``.
_SENSITIVE_TOKENS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
        "authorization",
        "cookie",
        "session",
        "credential",
        "credentials",
        "private",
        "passphrase",
        "apikey",
        "api",
        "access",
        "refresh",
        "bearer",
        "otp",
        "mfa",
        "pin",
        "signature",
        "auth",
        "csrf",
        "xsrf",
        "passcode",
    }
)
_KEY_TOKENS = re.compile(r"[^a-z0-9]+")
# JSON/JS style keys are frequently camelCase or PascalCase. Tokenising only on
# non-alphanumerics turns ``clientSecret`` into one opaque token that slips
# past the sensitive-token set even though ``client_secret`` is caught. Split
# lower/digit->upper transitions (and acronym boundaries) first so both spellings
# are treated identically.
_CAMEL_LOWER_UPPER_RE = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")

_INLINE_SECRET_RE = re.compile(
    r"\b(password(?:[_-]?hash)?|passwd|pwd|secret(?:[_-]?key)?|token|"
    r"api[_-]?key|apikey|authorization|cookie|session(?:[_-]?id)?|"
    r"credential|private[_-]?key|passphrase|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|"
    r"encryption[_-]?key|signing[_-]?key)\b[\"']?\s*[:=]\s*(?!\[REDACTED\])"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;}&)\]]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_NAMED_PREFIX_RE = re.compile(
    r"\b(?:sk|rk|gh[pousr]|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b|"
    r"\bAKIA[A-Z0-9]{16}\b"
)
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)

# Log/audit text is untrusted free form input. A user whose value reached a log
# line can embed an ANSI CSI/OSC sequence or a raw C0/DEL control character and
# repaint, clear or forge entries in any terminal or browser log viewer. Strip
# escape sequences and render the remaining controls as visible ``\xNN`` so the
# information is preserved for forensics but is never executable.
#
# The C1 range (U+0080-U+009F) matters too: it contains its own CSI (U+009B),
# OSC (U+009D), ST (U+009C) and NEL (U+0085). Filters that only look for an ESC
# (0x1B) introducer let those through, and terminals / browser viewers that
# decode UTF-8 treat C1 controls like their ESC-based equivalents. Escaping the
# whole C1 range here closes that bypass.
_ESCAPE_SEQUENCE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def scrub_control_characters(value: Any) -> str:
    """Return log text with escape sequences and control characters neutralised.

    Unlike :func:`scrub_text` (which redacts *secrets*), this is a presentation
    guard: it is applied to already-redacted free text just before it is handed
    to a log viewer or a logger, so an embedded newline, carriage return,
    backspace, C1 control or ANSI escape cannot alter the surrounding display.
    """
    text = "" if value is None else str(value)
    text = _ESCAPE_SEQUENCE_RE.sub("", text)
    return _CONTROL_CHARACTER_RE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def _is_sensitive_key(key: Any) -> bool:
    name = str(key)
    # Normalise camelCase/PascalCase/acronym keys before tokenising, otherwise
    # ``clientSecret`` / ``accessToken`` are single tokens that evade the set
    # while their snake_case equivalents are redacted.
    name = _CAMEL_ACRONYM_RE.sub(r"\1_\2", name)
    name = _CAMEL_LOWER_UPPER_RE.sub(r"\1_\2", name)
    tokens = [token for token in _KEY_TOKENS.split(name.lower()) if token]
    return any(token in _SENSITIVE_TOKENS for token in tokens)


def scrub_text(text: str, _budget: Optional[List[int]] = None) -> str:
    """Redact secret-looking substrings from a free-form string."""
    if _budget is None:
        _budget = [_MAX_ITEMS, _MAX_CHARS]
    if len(text) > _MAX_STRING_CHARS:
        text = text[:_MAX_STRING_CHARS] + "[TRUNCATED]"
    _budget[1] -= len(text)
    if _budget[1] <= 0:
        return REDACTED

    stripped = text.strip()
    # JSON-in-a-string is common in audit details. Scrub it structurally so
    # nested keys are redacted by name instead of by fragile text matching.
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            pass
        else:
            return json.dumps(
                redact_secrets(parsed, _depth=1, _budget=_budget), sort_keys=True
            )

    # Header-style tokens first: the inline ``key=value`` pass would otherwise
    # consume just the word "Bearer" and leave the credential itself behind.
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _PEM_RE.sub(REDACTED, text)
    text = _JWT_RE.sub(REDACTED, text)
    text = _NAMED_PREFIX_RE.sub(REDACTED, text)
    text = _INLINE_SECRET_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return text


def redact_secrets(
    value: Any, *, _depth: int = 0, _budget: Optional[List[int]] = None
) -> Any:
    """Recursively scrub secrets from ``value``.

    Mappings keep their structure; values under sensitive keys become
    ``[REDACTED]``. Lists/tuples/sets are rebuilt with scrubbed members.
    Strings get both key-less text scrubbing and an attempt at structured JSON
    scrub. Scalars pass through unchanged. Unknown objects are converted to a
    scrubbed ``repr`` rather than being embedded verbatim.
    """
    if _budget is None:
        _budget = [_MAX_ITEMS, _MAX_CHARS]
    if _depth > _MAX_DEPTH or _budget[0] <= 0:
        return REDACTED
    _budget[0] -= 1

    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                result[key] = REDACTED
                continue
            result[key] = redact_secrets(item, _depth=_depth + 1, _budget=_budget)
            if _budget[0] <= 0:
                break
        return result

    if isinstance(value, (list, tuple, set, frozenset)):
        items = []
        for item in value:
            items.append(redact_secrets(item, _depth=_depth + 1, _budget=_budget))
            if _budget[0] <= 0:
                break
        return items

    if isinstance(value, str):
        return scrub_text(value, _budget)

    if isinstance(value, bytes):
        return scrub_text(value.decode("utf-8", errors="replace"), _budget)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return scrub_text(repr(value), _budget)


__all__ = ["REDACTED", "redact_secrets", "scrub_text", "scrub_control_characters"]
