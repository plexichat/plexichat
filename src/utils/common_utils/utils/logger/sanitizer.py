import re
from typing import Any, Dict, List, Union

# Patterns for sensitive data
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
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
        # Scan string for emails and mask them
        def email_repl(match):
            return mask_email(match.group(0))

        return EMAIL_PATTERN.sub(email_repl, data)
    return data


def sanitize_log_message(message: str) -> str:
    """Sanitizes sensitive information from a log message string."""
    if not isinstance(message, str):
        return str(message)

    # Mask emails
    def email_repl(match):
        return mask_email(match.group(0))

    message = EMAIL_PATTERN.sub(email_repl, message)

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
