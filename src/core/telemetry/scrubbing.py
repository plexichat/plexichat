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
    }
)
_KEY_TOKENS = re.compile(r"[^a-z0-9]+")

_INLINE_SECRET_RE = re.compile(
    r"\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|"
    r"cookie|session|credential|private[_-]?key|passphrase|client_secret|"
    r"access_token|refresh_token)\b\s*[:=]\s*(?!\[REDACTED\])"
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


def _is_sensitive_key(key: Any) -> bool:
    tokens = [token for token in _KEY_TOKENS.split(str(key).lower()) if token]
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


__all__ = ["REDACTED", "redact_secrets", "scrub_text"]
