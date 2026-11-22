import re
from typing import Any, Dict, List, Union

# Patterns for sensitive data
# Atomic groups ((?>...)) prevent backtracking, and bounded quantifiers keep
# per-position work constant, so long non-matching input (e.g. a 100k-char
# log line of repeated letters) scans in linear time instead of quadratic
# (log injection as a cheap DoS vector). Local part max 64 chars and domain
# max 255 chars per RFC 5321. Python 3.11+ required for atomic groups.
EMAIL_PATTERN = re.compile(
    r"(?>[a-zA-Z0-9_.+-]{1,64})@(?>[a-zA-Z0-9-]{1,255})\.[a-zA-Z0-9-.]{1,255}"
)

# Trigger substrings for the secret-masking patterns below. Cheap substring
# pre-checks avoid running the regex engines over long lines that cannot
# match (regex scanning is linear, but this keeps constant factors tiny).
_KEYWORD_TRIGGERS = (
    "authorization",
    "bearer",
    "password",
    "passwd",
    "secret",
    "token",
    "key",
    "auth",
    "api_key",
    "client_secret",
    "session_id",
    "cookie",
)
# Email domains to never mask (official Plexichat addresses)
EMAIL_ALLOWLIST_DOMAINS = {"plexichat.com", "plexichat.internal"}
# Common keys for sensitive data in dictionaries
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "auth",
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "key",
    "api_key",
    "private_key",
    "jwt",
    "bearer",
    "totp_secret",
    "session_id",
    "cookie",
    "email",
    "username",
    "ip_address",
    "user_agent",
    "date_of_birth",
    "dob",
}


def mask_email(email: str) -> str:
    """Masks an email address (e.g., user@example.com -> u***@example.com)."""
    if not email or "@" not in email:
        return email
    try:
        user, domain = email.split("@", 1)
        if domain.lower() in EMAIL_ALLOWLIST_DOMAINS:
            return email
        if len(user) <= 1:
            return f"*@{domain}"
        return f"{user[0]}***@{domain}"
    except Exception:
        return "***@***"


def mask_string(value: str) -> str:
    """Masks a sensitive string partially."""
    if not value:
        return value
    if len(value) <= 8:
        return "********"
    # SECURITY: Expose only 2 chars on each end to preserve entropy
    return f"{value[:2]}***{value[-2:]}"


def sanitize_value(key: str, value: Any) -> Any:
    """Sanitizes a single value based on its key."""
    if not isinstance(value, str):
        return value

    key_lower = key.lower()

    # Check if key is sensitive
    if any(sk in key_lower for sk in SENSITIVE_KEYS):
        return mask_string(value)

    # Check if value looks like an email
    if EMAIL_PATTERN.fullmatch(value):
        return mask_email(value)

    return value


# Credential-only key set for logging %-style mapping args. ``SENSITIVE_KEYS``
# above is intentionally broad (it scrubs PII such as usernames and emails),
# but applying it at the formatting layer would destroy ordinary operational
# logs. These are the values that must never be written verbatim.
_CREDENTIAL_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "api_key",
    "cookie",
    "session",
    "credential",
)
_CREDENTIAL_KEYS_EXACT = frozenset(
    {"authorization", "auth", "bearer", "private_key", "set-cookie"}
)


def _is_credential_key(key: Any) -> bool:
    lowered = str(key).lower()
    return lowered in _CREDENTIAL_KEYS_EXACT or any(
        part in lowered for part in _CREDENTIAL_KEY_PARTS
    )


def sanitize_log_args(args: Any) -> Any:
    """Redact credential-bearing values in logging ``%``-style args by key.

    ``logging`` substitutes ``record.args`` only after every filter has run, so
    by the time the rendered line can be scanned the mapping's key names are
    gone. Mask by name here instead. Only mappings (and mappings nested in the
    positional tuple) are touched; plain string args are handled by
    ``sanitize_log_message`` on the rendered line.
    """
    if isinstance(args, dict):
        return {
            key: mask_string(value)
            if isinstance(value, str) and _is_credential_key(key)
            else value
            for key, value in args.items()
        }
    if isinstance(args, tuple):
        return tuple(
            sanitize_log_args(arg) if isinstance(arg, dict) else arg for arg in args
        )
    return args


def sanitize_data(data: Union[Dict, List, str, Any]) -> Any:
    """
    Recursively sanitizes sensitive data in dictionaries, lists, or strings.
    """
    if isinstance(data, dict):
        return {k: sanitize_value(k, sanitize_data(v)) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, str):
        # Scan string for emails and mask them (skip when no '@' can match)
        if "@" in data:

            def email_repl(match):
                return mask_email(match.group(0))

            return EMAIL_PATTERN.sub(email_repl, data)
        return data
    return data


# Log/audit text is untrusted free-form input. A caller-controlled value (e.g.
# a percent-decoded URL path in an access-log line, or an exception message that
# embeds user input) can carry a raw newline/carriage return or an ANSI CSI/OSC
# sequence and forge, clear or repaint entries in a file, terminal or log
# viewer (CWE-117). The admin log viewer and telemetry already apply the shared
# ``scrub_control_characters`` presentation guard, but the on-disk log written
# by ``SanitizingFilter`` must be neutralised at the sink as well. Kept
# self-contained (no ``src.core`` import) because this module sits below the
# application package in the import graph and is used during logging setup;
# the semantics intentionally mirror the telemetry guard.
_ESCAPE_SEQUENCE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
# Keep this set in lockstep with ``hardening.text.CONTROL_CHARACTERS``. Beyond
# C0/DEL/C1, the Unicode LINE/PARAGRAPH SEPARATORs (U+2028/U+2029) are treated as
# line breaks by log shippers, proxies and browser viewers, and the bidi /
# invisible format controls let one value render as another. The request-schema
# boundary and the audit helpers already reject this whole set; a log line that
# leaves any of it in place can be read as more than one entry downstream even
# though it was written as one. This module sits below the application package,
# so mirror the set locally rather than importing ``src.core`` here.
_CONTROL_CHARACTER_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f"  # C0, DEL and C1 controls (incl. NEL)
    r"\u061c\u200e\u200f\u070f"  # Arabic/LR/RL marks + Syriac abbreviation mark
    r"\u202a-\u202e"  # bidi embedding and overrides
    r"\u2066-\u206f"  # bidi isolates + legacy directional formats
    r"\u2028\u2029\u2060\ufeff]"  # line/para separators, word joiner, BOM
)


def _scrub_log_control_characters(value: Any) -> str:
    """Strip ANSI escapes and render control characters visibly.

    C0/C1/DEL render as ``\\xNN``; the wider Unicode separators and bidi
    controls render as ``\\uNNNN`` so a shipper that normalises U+2028/U+2029 to
    a newline cannot forge an extra entry out of a single request value.
    """
    text = "" if value is None else str(value)
    text = _ESCAPE_SEQUENCE_RE.sub("", text)

    def _escape(match):
        codepoint = ord(match.group())
        return f"\\x{codepoint:02x}" if codepoint <= 0xFF else f"\\u{codepoint:04x}"

    return _CONTROL_CHARACTER_RE.sub(_escape, text)


# Query-string parameters whose VALUES are bearer credentials or secret
# material. The keyword patterns in ``sanitize_log_message`` only fire when one
# of their trigger words appears anywhere in the line and only mask a fixed set
# of key names, so a bare ``?code=...``, ``&sig=...`` or ``&state=...`` survived
# verbatim into the world-readable log files (redirect URLs, proxy URLs, signed
# media URLs, OAuth/federation URLs, Referers). Redact by key instead, but only
# in a query-like context so ordinary diagnostics such as ``error code=500`` or
# ``AdminID=5 Action=login`` keep their values.
_QUERY_SECRET_KEYS = frozenset(
    {
        "code",
        "sig",
        "signature",
        "state",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "api_key",
        "key",
        "auth",
        # Unambiguous aliases already covered by the keyword pass; included so
        # a query context is redacted even when no keyword trigger is present.
        "authorization",
        "client_secret",
        "session_id",
        "cookie",
        "passwd",
    }
)
_QUERY_SECRET_MARKER = "********"

# A ``key=value`` pair introduced by ``?``/``&``, at a token boundary (start of
# string, whitespace, quote, parenthesis), with an optional quoted value. The
# value stops at ``&``/``#``/``?``/whitespace/quotes so a pair never swallows the
# next one. ``?`` is significant: a nested URL in a redirect/OAuth parameter
# (e.g. ``?redirect=https://x/cb?code=SECRET``) would otherwise be consumed as
# the outer value, leaving the inner secret unredacted. Whitespace around ``=``
# is tolerated to match the keyword pass's ``[\"'\s]*[:=]\s*`` semantics.
# Bounded character classes keep the scan linear-time.
_QUERY_PARAM_RE = re.compile(
    r"""(?ix)
    (?P<lead>[?&]|^|(?<=[\s"'(]))
    (?P<key>[A-Za-z0-9_.\-]+)
    \s*=\s*
    (?P<value>"[^"]*"|'[^']*'|[^&#?\s"'<>]*)
    """
)


def _redact_query_secrets(message: str) -> str:
    """Mask values of sensitive keys in query-like ``key=value`` pairs.

    "Query-like" means the pair is introduced by ``?`` or ``&``, or it is the
    first pair of an ``&``-separated run (a bare decoded query string). A lone
    ``key=value`` with no query marker is left alone -- the keyword pass above
    already handles that diagnostic shape by key.
    """

    def repl(match):
        key = match.group("key").lower()
        if key not in _QUERY_SECRET_KEYS:
            return match.group(0)
        lead = match.group("lead")
        query_like = lead in ("?", "&") or message[match.end() : match.end() + 1] == "&"
        if not query_like:
            return match.group(0)
        return f"{lead}{match.group('key')}={_QUERY_SECRET_MARKER}"

    return _QUERY_PARAM_RE.sub(repl, message)


def sanitize_log_message(message: str) -> str:
    """Sanitizes sensitive information from a log message string."""
    if not isinstance(message, str):
        return _scrub_log_control_characters(message)

    # Mask emails — skipped entirely when no '@' can match (keeps long
    # non-matching lines linear-time).
    if "@" in message:

        def email_repl(match):
            return mask_email(match.group(0))

        message = EMAIL_PATTERN.sub(email_repl, message)

    if any(kw in message.lower() for kw in _KEYWORD_TRIGGERS):
        patterns = [
            (
                r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+",
                "Authorization: Bearer ********",
            ),
            (r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ********"),
            (
                r"(?i)(password|passwd|secret|token|key|auth|authorization|access_token|refresh_token|api_key|client_secret|session_id|cookie)[\"'\s]*[:=]\s*[\"']([^\"\\]+)[\"\\]",
                r"\1: ********",
            ),
            (
                r"(?i)(password|passwd|secret|token|key|auth|authorization|access_token|refresh_token|api_key|client_secret|session_id|cookie)[\"'\s]*[:=]\s*([^\s,]+)",
                r"\1: ********",
            ),
        ]
        for pattern, repl in patterns:
            message = re.sub(pattern, repl, message)

    # Query-string credentials (``?code=``, ``&sig=``, ``&state=``, ...) are not
    # covered by the keyword pass above: those keys are not trigger words and
    # their values are not keyword-shaped. Redact by key in query-like pairs.
    # Runs unconditionally -- there is no cheap substring trigger that covers
    # all of ``code``/``sig``/``state`` without over-matching ordinary text.
    if "=" in message:
        message = _redact_query_secrets(message)

    # Final step: no matter what the redaction passes produced, never let raw
    # control characters or escape sequences reach a log sink where they could
    # forge a new entry or repaint the viewer (CWE-117).
    return _scrub_log_control_characters(message)
