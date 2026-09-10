"""
Session cookie helpers.

Implements the HttpOnly-cookie half of Plexichat's dual-mode authentication.

M9: the session token is delivered to browsers ONLY as an HttpOnly cookie
that JavaScript cannot read, so an XSS payload cannot exfiltrate a
long-lived credential. The JSON body of login/register/refresh/2FA/OAuth/
passkey success responses carries NO raw token by default — only a status
marker and the session lifetime (``expires_in``, mirroring the cookie
Max-Age). A raw bearer token is returned in the body SOLELY when a
non-browser client explicitly opts in with the
``X-Plexichat-Token-Mode: bearer`` request header (bots/CLI/self-test —
callers that cannot persist cookies).

Cookie-authenticated requests are CSRF-protected: the API middleware only
honours the cookie for state-changing methods when the client's
``X-Plexichat-Request`` marker header is present (custom headers cannot be
attached cross-origin without a CORS preflight approval).
"""

from typing import Optional

from fastapi import Request, Response

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

    Secure-by-default: the long-lived HttpOnly session credential must not
    travel over plain HTTP unless an operator explicitly opts out with
    ``authentication.security.session_cookie_secure: false`` (intended for
    localhost / plain-HTTP dev deployments only).
    """
    try:
        return bool(
            _auth_config().get("security", {}).get("session_cookie_secure", True)
        )
    except Exception:
        return True


# M9: explicit non-browser opt-in for receiving the raw session token in
# the JSON body. Browsers (the default) get the token ONLY via the
# HttpOnly cookie; JS-readable bodies never carry it, so XSS cannot steal
# a long-lived credential out of a login/register/refresh response.
TOKEN_IN_BODY_HEADER = "X-Plexichat-Token-Mode"
TOKEN_IN_BODY_BEARER = "bearer"


def wants_token_in_body(request: Optional[Request]) -> bool:
    """Whether the caller explicitly requested a raw bearer token in the body.

    Only a literal ``X-Plexichat-Token-Mode: bearer`` header opts in
    (case-insensitive value). Anything else — including a missing header —
    means cookie flow: the token travels solely via the HttpOnly cookie.
    """
    try:
        if request is None:
            return False
        return (
            request.headers.get(TOKEN_IN_BODY_HEADER) or ""
        ).strip().lower() == TOKEN_IN_BODY_BEARER
    except Exception:
        return False


def session_token_for_response(
    request: Optional[Request], token: Optional[str]
) -> Optional[str]:
    """Return the raw token for the JSON body iff the caller opted in.

    M9: cookie-flow responses MUST pass the minted token through here so
    the body carries ``None`` while the HttpOnly cookie (set separately
    via :func:`set_session_cookie`) remains the sole credential channel.
    """
    if not token:
        return None
    return token if wants_token_in_body(request) else None


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
    "TOKEN_IN_BODY_HEADER",
    "session_cookie_max_age",
    "session_cookie_secure",
    "set_session_cookie",
    "clear_session_cookie",
    "get_session_token_from_cookies",
    "wants_token_in_body",
    "session_token_for_response",
]
