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


def sanitize_log_message(message: str) -> str:
    """Sanitizes sensitive information from a log message string."""
    if not isinstance(message, str):
        return str(message)

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

    return message
