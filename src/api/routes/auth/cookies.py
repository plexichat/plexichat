"""
Session cookie helpers.

Implements the HttpOnly-cookie half of Plexichat's dual-mode authentication:
the browser receives the session token both in the JSON body (Bearer, for
the WebSocket gateway) and as an HttpOnly cookie that JavaScript cannot
read, so an XSS payload cannot exfiltrate a long-lived credential.

Cookie-authenticated requests are CSRF-protected: the API middleware only
honours the cookie for state-changing methods when the client's
``X-Plexichat-Request`` marker header is present (custom headers cannot be
attached cross-origin without a CORS preflight approval).
"""

from typing import Optional

from fastapi import Response

try:
    import utils.config as config
except ImportError:  # pragma: no cover - config always available in practice
    config = None

# Name already read by src/api/app.py for signed media URLs.
SESSION_COOKIE_NAME = "plexichat_token"


def _auth_config() -> dict:
    try:
        return (config.get("authentication", {}) or {}) if config else {}
    except Exception:
        return {}


def _sessions_config() -> dict:
    return _auth_config().get("sessions", {}) or {}


def session_cookie_max_age() -> int:
    """Cookie lifetime mirrors the configured session expiry (hours -> seconds)."""
    expire_hours = _sessions_config().get("expire_hours", 720)
    try:
        return max(1, int(expire_hours)) * 3600
    except (TypeError, ValueError):
        return 720 * 3600


def session_cookie_secure() -> bool:
    """Whether cookies carry the Secure flag.

    Defaults to False so plain-HTTP self-hosted deployments keep working;
    production deployments behind TLS should set
    ``authentication.security.session_cookie_secure: true``.
    """
    return bool(_auth_config().get("security", {}).get("session_cookie_secure", False))


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the HttpOnly session cookie to a successful auth response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=session_cookie_max_age(),
        httponly=True,
        secure=session_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the session cookie (logout / invalidation)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=session_cookie_secure(),
        samesite="lax",
    )


def get_session_token_from_cookies(cookies: Optional[dict]) -> Optional[str]:
    """Extract the raw session token from a request cookie jar."""
    if not cookies:
        return None
    token = cookies.get(SESSION_COOKIE_NAME)
    return token if isinstance(token, str) and token else None


__all__ = [
    "SESSION_COOKIE_NAME",
    "session_cookie_max_age",
    "session_cookie_secure",
    "set_session_cookie",
    "clear_session_cookie",
    "get_session_token_from_cookies",
]
