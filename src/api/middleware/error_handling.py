"""
Error handling middleware - Convert exceptions to HTTP responses.
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.types import ASGIApp, Receive, Send, Scope, Message

from typing import Any, Dict, Mapping, Optional, cast
import re
import traceback
import utils.config as config
import utils.logger as logger

from src.core.security.errors import SecurityRejection

from .request_context import cached_is_self_test
from .logging import _safe_log_path

try:
    from utils.logger import sanitize_log_message
except ImportError:
    # Fallback if common_utils utilities are not available
    def sanitize_log_message(message: str) -> str:
        return message


ERROR_MAPPINGS = {
    "NotFoundError": 404,
    "AccessDeniedError": 403,
    "PermissionDeniedError": 403,
    "FileUploadError": 403,
    "InvalidCredentialsError": 401,
    "TokenExpiredError": 401,
    "TokenInvalidError": 401,
    "AccountLockedError": 403,
    "AccountDisabledError": 403,
    "UserExistsError": 409,
    "AlreadyExistsError": 409,
    "ExistsError": 409,
    "ValidationError": 400,
    "InvalidError": 400,
    "LimitError": 400,
    "AttachmentLimitError": 400,
    "ChannelFullError": 507,
    "LicenseFeatureError": 403,
    "UserNotInChannelError": 404,
    "UserAlreadyInChannelError": 409,
    "ChannelTypeError": 400,
    "NotSpeakerError": 403,
    "AlreadySpeakerError": 409,
    "RateLimitError": 429,
    "BlockedError": 403,
    "LockedError": 423,
    "ArchivedError": 410,
    "InvalidRecipientError": 404,
}


# Generic, stable client-facing messages per mapped exception type (M6).
# Mapped exceptions NEVER echo str(exc) verbatim: internal validation detail,
# filesystem paths, SQL fragments, or config values must not reach clients.
# The original detail is logged server-side at the catch site instead.
GENERIC_ERROR_MESSAGES = {
    "NotFoundError": "Resource not found",
    "AccessDeniedError": "Access denied",
    "PermissionDeniedError": "Permission denied",
    "FileUploadError": "File upload failed",
    "InvalidCredentialsError": "Invalid credentials",
    "TokenExpiredError": "Authentication token has expired",
    "TokenInvalidError": "Invalid authentication token",
    "AccountLockedError": "Account is locked",
    "AccountDisabledError": "Account is disabled",
    "UserExistsError": "User already exists",
    "AlreadyExistsError": "Resource already exists",
    "ExistsError": "Resource already exists",
    "ValidationError": "Validation failed",
    "InvalidError": "Invalid request",
    "LimitError": "Request limit exceeded",
    "AttachmentLimitError": "Attachment limit exceeded",
    "ChannelFullError": "Channel is full",
    "LicenseFeatureError": "Feature not available",
    "UserNotInChannelError": "User is not in the channel",
    "UserAlreadyInChannelError": "User is already in the channel",
    "ChannelTypeError": "Invalid channel type",
    "NotSpeakerError": "Speaker permission required",
    "AlreadySpeakerError": "Already a speaker",
    "RateLimitError": "Rate limit exceeded",
    "BlockedError": "Action is blocked",
    "LockedError": "Resource is locked",
    "ArchivedError": "Resource is archived",
    "InvalidRecipientError": "Invalid recipient",
}

_FALLBACK_MESSAGE_BY_STATUS = {
    400: "Invalid request",
    401: "Authentication required",
    403: "Access denied",
    404: "Resource not found",
    409: "Resource already exists",
    410: "Resource is archived",
    413: "Request too large",
    423: "Resource is locked",
    429: "Rate limit exceeded",
    507: "Channel is full",
}


def _match_mapped_pattern(exc_name: str) -> str | None:
    """Return the ERROR_MAPPINGS key matching an exception class name.

    Exact matches win; otherwise the longest suffix pattern wins so that
    e.g. ``RateLimitError`` maps to its own entry rather than the shorter
    ``LimitError`` suffix (which previously shadowed it and yielded 400
    instead of the mapped 429).
    """
    if exc_name in ERROR_MAPPINGS:
        return exc_name
    best: str | None = None
    for pattern in ERROR_MAPPINGS:
        if exc_name.endswith(pattern) and (best is None or len(pattern) > len(best)):
            best = pattern
    return best


def _generic_message_for_exception(exc: Exception, status_code: int) -> str:
    """Return the generic per-type message for a mapped exception."""
    pattern = _match_mapped_pattern(type(exc).__name__)
    if pattern is not None:
        code = ERROR_MAPPINGS[pattern]
        return GENERIC_ERROR_MESSAGES.get(
            pattern, _FALLBACK_MESSAGE_BY_STATUS.get(code, "Invalid request")
        )
    return _FALLBACK_MESSAGE_BY_STATUS.get(status_code, "Internal server error")


def get_status_code_for_exception(exc: Exception) -> int:
    """Determine HTTP status code based on exception type."""
    pattern = _match_mapped_pattern(type(exc).__name__)
    if pattern is not None:
        return ERROR_MAPPINGS[pattern]

    return 500


def _debug_detail_allowed(request: Any) -> bool:
    """Whether this request may receive DEBUG-LEVEL error detail.

    M5: ``api.debug`` alone is NOT sufficient — it is enabled by default
    (config_defaults.py) and would otherwise leak raw 5xx exception text
    to any caller on a deployment that never flipped the flag. Debug
    detail therefore additionally requires the request to be a valid
    internal/self-test request: localhost peer AND the constant-time
    compared ``X-Plexichat-Internal-Secret`` header
    (``src.api.is_self_test_request``). Both signals fail closed when
    absent — a spoofed header from a non-localhost peer, or any request
    when no internal secret is provisioned, gets generic messages only.
    """
    try:
        if not config.get("api", {}).get("debug", False):
            return False
    except Exception:
        return False
    try:
        return bool(cached_is_self_test(request))
    except Exception:
        return False


def _sanitize_error_message(
    code: int, message: str, debug: bool, debug_detail: Optional[bool] = None
) -> str:
    safe_message = sanitize_log_message(message)
    # ``debug_detail`` is the authoritative gate (api.debug AND
    # localhost+secret). Legacy ``debug`` alone no longer unlocks raw
    # internal messages (M5).
    effective_debug = debug if debug_detail is None else debug_detail
    if effective_debug:
        return safe_message
    if code >= 500:
        return "Internal server error"
    if re.search(
        r"\b(select|insert|update|delete|from|where|join)\b", safe_message, re.I
    ):
        return "Invalid request"
    return safe_message


def _safe_error_message(
    exc: Exception, status_code: int, debug_detail: bool = False
) -> str:
    """Return a client-safe message for a mapped exception.

    M6: mapped exceptions NEVER echo ``str(exc)`` verbatim — not even for
    project-owned (``src.*``) exceptions, whose messages can embed
    validation detail, filesystem paths, SQL fragments, or config values.
    Every mapped type gets a generic, stable per-type message; the
    original detail is logged server-side at the catch site instead.
    When ``debug_detail`` is True (api.debug AND a valid internal
    self-test request per M5), the raw message is returned so internal
    diagnostics keep working; otherwise the generic message applies.
    """
    if debug_detail:
        return str(exc)
    pattern = _match_mapped_pattern(type(exc).__name__)
    if pattern is not None:
        return GENERIC_ERROR_MESSAGES.get(
            pattern,
            _FALLBACK_MESSAGE_BY_STATUS.get(status_code, "Invalid request"),
        )
    if status_code >= 500:
        return "Internal server error"
    return _generic_message_for_exception(exc, status_code)


def _redact_validation_error_inputs(errors: Any) -> list:
    """Return Pydantic validation errors safe to write to the server log.

    ``exc.errors()`` embeds the rejected request value under ``input``.  On
    auth endpoints that value is a password, reset token or 2FA code.  The
    shared ``sanitize_log_message`` keyword patterns cannot mask it because
    the dict key is ``input`` (not ``password``/``token``), so the raw secret
    would otherwise be persisted verbatim (CWE-532).  Drop the submitted value
    while keeping the diagnostic ``type``/``loc``/``msg`` fields.  ``ctx`` can
    echo the same value via a custom validator, so it is scrubbed too; any
    unexpected shape is omitted outright rather than logged verbatim (fail
    closed).
    """
    safe: list = []
    if not isinstance(errors, (list, tuple)):
        return safe
    for error in errors:
        if not isinstance(error, dict):
            continue
        scrubbed = dict(error)
        for key in ("input", "ctx"):
            if key in scrubbed:
                scrubbed[key] = "<redacted>"
        safe.append(scrubbed)
    return safe


def _merge_error_headers(
    cors_headers: Dict[str, str], exc_headers: Optional[Mapping[str, str]]
) -> Dict[str, str]:
    """Merge exception-provided headers (e.g. ``Allow``) into CORS headers."""
    if not exc_headers:
        return cors_headers
    return {**cors_headers, **exc_headers}


def format_error_response(code: int, message: str, debug: bool = False) -> dict:
    """Format error response in standard format."""
    safe_message = _sanitize_error_message(code, message, debug)
    return {"error": {"code": code, "message": safe_message}}


# Mirrors the safe fallback in ``src.api.config.get_api_config``: an omitted
# allowlist is never treated as "allow any origin".
_DEFAULT_ERROR_CORS_ORIGINS = [
    "https://plexichat.com",
    "https://app.plexichat.com",
    "https://api.plexichat.com",
    "https://www.plexichat.com",
]
_DEFAULT_ERROR_CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "Accept",
    "X-API-Access-Token",
    "X-Plexichat-Request",
]
_DEFAULT_ERROR_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _get_cors_headers(request: Request) -> dict:
    """Build CORS headers for error responses using the configured policy.

    Error responses must obey the same CORS rules as successful responses.
    The old helper defaulted an omitted ``cors_origins`` to ``["*"]`` and
    reflected whatever was listed verbatim, so a deployment that never set an
    allowlist granted ``Access-Control-Allow-Origin: *`` *together with*
    ``Access-Control-Allow-Credentials: true`` on every 4xx/5xx -- the exact
    combination the API config rejects at build time. Apply the same wildcard
    hardening here and fail closed whenever the effective policy is unclear.
    """
    cors_origins = list(_DEFAULT_ERROR_CORS_ORIGINS)
    cors_allow_headers = list(_DEFAULT_ERROR_CORS_HEADERS)
    cors_methods = list(_DEFAULT_ERROR_CORS_METHODS)
    allow_credentials = False  # fail closed until configuration says otherwise
    allow_wildcard = False
    try:
        api_conf = config.get("api", {}) or {}
        raw_origins = api_conf.get("cors_origins")
        if raw_origins:
            # A string is a single origin; a sequence is the allowlist. Any
            # other shape is treated as empty (fail closed).
            if isinstance(raw_origins, str):
                cors_origins = [raw_origins]
            elif isinstance(raw_origins, (list, tuple)):
                cors_origins = list(raw_origins)
            else:
                cors_origins = []
        configured_headers = api_conf.get("cors_allow_headers")
        if configured_headers:
            cors_allow_headers = list(configured_headers)
        configured_methods = api_conf.get("cors_allow_methods")
        if configured_methods:
            cors_methods = list(configured_methods)
        allow_credentials = bool(api_conf.get("cors_allow_credentials", True))
        allow_wildcard = bool(api_conf.get("allow_wildcard_cors", False))
    except Exception:
        # Error handling must fail closed if configuration cannot be read;
        # reflecting an arbitrary Origin with credentials would weaken the
        # authentication/CSRF boundary precisely during failures.
        cors_origins = []
        allow_credentials = False

    # Mirror ``get_api_config``'s wildcard hardening: a wildcard is useless
    # unless explicitly opted into, and may never carry credentials (it would
    # otherwise let any site read credentialed error responses cross-origin).
    has_wildcard = any(isinstance(o, str) and o.strip() == "*" for o in cors_origins)
    if has_wildcard and (not allow_wildcard or allow_credentials):
        return {}
    if has_wildcard:
        allow_credentials = False

    allowed = {
        o.strip().rstrip("/")
        for o in cors_origins
        if isinstance(o, str) and o.strip() and o.strip() != "*"
    }

    raw_origin = request.headers.get("origin", "")
    if not raw_origin or not isinstance(raw_origin, str):
        return {}
    origin = raw_origin.strip().rstrip("/")

    common_headers = {
        "Access-Control-Allow-Methods": ", ".join(cors_methods),
        "Access-Control-Allow-Headers": ", ".join(cors_allow_headers),
    }
    if has_wildcard:
        # Anonymous wildcard: a literal '*' is safe precisely because no
        # credentials are attached.
        return {"Access-Control-Allow-Origin": "*", **common_headers}
    if origin not in allowed:
        return {}
    headers = {"Access-Control-Allow-Origin": origin, **common_headers}
    if allow_credentials:
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers


class ErrorHandlingMiddleware:
    """Middleware to catch all exceptions and return JSON responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # If response has already started, we CANNOT send a new JSON response
            # because http.response.start has already been sent to the client.
            if response_started:
                import utils.logger as logger

                logger.error(
                    f"Exception occurred after response started: {type(exc).__name__}: {exc}. "
                    "Cannot send error JSON as headers were already transmitted.",
                    exc_info=True,
                )
                # Raising here will likely cause the server to close the connection
                raise exc

            # IMPORTANT: This middleware wraps the whole app and therefore will see
            # exceptions BEFORE FastAPI's exception handler registry.
            # If we don't special-case HTTPException, it will be converted to 500.
            request = Request(scope)

            if isinstance(exc, StarletteHTTPException):
                http_exc = cast(StarletteHTTPException, exc)
                headers = _merge_error_headers(
                    _get_cors_headers(request), http_exc.headers
                )

                debug_detail = _debug_detail_allowed(request)

                detail = http_exc.detail
                if isinstance(detail, dict) and "error" in detail:
                    detail_dict = cast(Dict[str, Any], detail)
                    error = detail_dict.get("error")
                    if isinstance(error, dict):
                        msg = error.get("message")
                        if msg and isinstance(msg, str):
                            error["message"] = _sanitize_error_message(
                                http_exc.status_code, msg, debug_detail, debug_detail
                            )
                        detail_dict["error"] = error

                    response = JSONResponse(
                        status_code=http_exc.status_code,
                        content=detail_dict,
                        headers=headers,
                    )
                    await response(scope, receive, send)
                    return

                response = JSONResponse(
                    status_code=http_exc.status_code,
                    content=format_error_response(
                        http_exc.status_code, str(detail), debug_detail
                    ),
                    headers=headers,
                )
                await response(scope, receive, send)
                return

            if isinstance(exc, RequestValidationError):
                headers = _get_cors_headers(request)
                # Documented contract: validation errors surface as 400
                # (docs/errors.md "Validation Errors (400)"), matching
                # ERROR_MAPPINGS' ValidationError -> 400.
                response = JSONResponse(
                    status_code=400,
                    content=format_error_response(400, "Request validation failed"),
                    headers=headers,
                )
                await response(scope, receive, send)
                return

            # Log the error details server-side
            import utils.logger as logger

            logger.error(
                f"Middleware caught exception: {type(exc).__name__}: {exc}",
                exc_info=True,
            )

            # Determine status code and message
            status_code = get_status_code_for_exception(exc)

            # M5: debug-level detail (raw messages, tracebacks) requires
            # api.debug AND localhost+secret — never the flag alone.
            debug_detail = _debug_detail_allowed(request)

            # M6: mapped exceptions get generic per-type messages; the
            # original detail was already logged server-side above and is
            # never echoed. Raw detail only for valid internal self-test
            # requests (M5 gate).
            message = _safe_error_message(exc, status_code, debug_detail)
            if not debug_detail:
                message = _sanitize_error_message(status_code, message, False)

            # Check for self-test debug mode (uses centralized validation)
            include_traceback = False

            selftest_config = config.get("selftest", {})
            is_selftest = cached_is_self_test(request)

            if selftest_config.get("capture_stack_traces", True) and is_selftest:
                if request.headers.get("X-Plexichat-SelfTest-Debug") == "true":
                    include_traceback = True

            # Get CORS headers
            headers = _get_cors_headers(request)

            # Create response content
            content = format_error_response(status_code, message, debug_detail)
            if include_traceback:
                content["error"]["traceback"] = "".join(traceback.format_exc())

            # Create response
            response = JSONResponse(
                status_code=status_code, content=content, headers=headers
            )

            await response(scope, receive, send)


def setup_exception_handlers(app: FastAPI):
    """Setup exception handlers for the FastAPI application."""

    @app.exception_handler(SecurityRejection)
    async def security_rejection_handler(request: Request, exc: SecurityRejection):
        """Map a security refusal to a 400 without leaking why it happened.

        The exception carries a stable ``code`` worth asserting on and logging;
        the message is static. ``str(exc)`` can name a resolved internal
        address, so it goes to the log and never to the client.
        """
        headers = _get_cors_headers(request)
        # SECURITY: the resolved path can carry a credential in a path segment
        # (notably the live webhook token in ``POST /webhooks/{id}/{token}``).
        # Route it through the same redactor the access log uses so an error
        # handler can never write that secret to the log sink.
        logger.warning(
            f"Security rejection on {request.method} "
            f"{_safe_log_path(request.scope, request.url.path)}: "
            f"{type(exc).__name__} code={exc.code} detail={exc}"
        )
        error: Dict[str, Any] = {
            "code": 400,
            "reason": exc.code,
            "message": exc.public_message,
        }
        if exc.field:
            error["field"] = exc.field
        return JSONResponse(status_code=400, content={"error": error}, headers=headers)

    @app.exception_handler(HTTPException)
    async def fastapi_http_exception_handler(request: Request, exc: HTTPException):
        """Handle FastAPI HTTP exceptions."""
        headers = _merge_error_headers(_get_cors_headers(request), exc.headers)

        debug_detail = _debug_detail_allowed(request)

        if isinstance(exc.detail, dict) and "error" in exc.detail:
            detail_dict = cast(Dict[str, Any], exc.detail)
            error = detail_dict.get("error")
            if isinstance(error, dict):
                msg = error.get("message")
                if msg and isinstance(msg, str):
                    error["message"] = _sanitize_error_message(
                        exc.status_code, msg, debug_detail, debug_detail
                    )
                detail_dict["error"] = error
            return JSONResponse(
                status_code=exc.status_code, content=detail_dict, headers=headers
            )

        return JSONResponse(
            status_code=exc.status_code,
            content=format_error_response(
                exc.status_code, str(exc.detail), debug_detail
            ),
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Handle HTTP exceptions."""
        detail = exc.detail
        headers = _merge_error_headers(_get_cors_headers(request), exc.headers)

        debug_detail = _debug_detail_allowed(request)

        if isinstance(detail, dict) and "error" in detail:
            detail_dict = cast(Dict[str, Any], detail)
            error = detail_dict.get("error")
            if isinstance(error, dict):
                msg = error.get("message")
                if msg and isinstance(msg, str):
                    error["message"] = _sanitize_error_message(
                        exc.status_code, msg, debug_detail, debug_detail
                    )
                detail_dict["error"] = error
            return JSONResponse(
                status_code=exc.status_code, content=detail_dict, headers=headers
            )

        return JSONResponse(
            status_code=exc.status_code,
            content=format_error_response(exc.status_code, str(detail), debug_detail),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        """Handle request validation errors."""
        errors = exc.errors()
        headers = _get_cors_headers(request)

        # Log validation detail server-side only; clients get a generic,
        # stable message (M6) so field names/paths/validators never leak.
        # SECURITY: exc.errors()['input'] holds the raw rejected value (a
        # password/token/code on auth routes). Redact it before logging so the
        # credential never reaches the log sink.
        # SECURITY: ``request.url.path`` is the *resolved* path. A rejected
        # webhook execution carries the live webhook token in its final path
        # segment; writing it verbatim here bypasses the access-log redactor
        # and leaks the credential on every validation failure. Use the shared
        # redactor (route template when available, token segment masked when
        # not) so this error path fails closed as well.
        logger.warning(
            f"Validation error on {request.method} "
            f"{_safe_log_path(request.scope, request.url.path)}: "
            f"{_redact_validation_error_inputs(errors)}"
        )
        # SECURITY: Do not read/log full request body to avoid memory DOS

        message = "Request validation failed"

        # Documented contract: validation errors surface as 400
        # (docs/errors.md "Validation Errors (400)").
        return JSONResponse(
            status_code=400,
            content=format_error_response(400, message),
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle all other exceptions."""
        status_code = get_status_code_for_exception(exc)
        # M6: never echo exception strings verbatim — generic per-type
        # message only. Detail goes to server logs, never the response
        # (raw detail only for valid internal self-test requests per M5).
        logger.error(
            f"General handler caught exception: {type(exc).__name__}: {exc}",
            exc_info=True,
        )
        debug_detail = _debug_detail_allowed(request)
        message = _safe_error_message(exc, status_code, debug_detail)
        if not debug_detail:
            message = _sanitize_error_message(status_code, message, False)
        headers = _get_cors_headers(request)

        return JSONResponse(
            status_code=status_code,
            content=format_error_response(status_code, message, debug_detail),
            headers=headers,
        )

    # /Join client-contract errors must reach the web client as a FLAT
    # {"error": {"code", "message"}} body (the client reads
    # ``body.error.message``), never nested under FastAPI's ``detail``
    # envelope. Imported lazily to avoid an import cycle with the routes
    # package.
    try:
        from ..routes.join import JoinApiError

        @app.exception_handler(JoinApiError)
        async def join_api_error_handler(request: Request, exc: JoinApiError):
            """Render /Join contract errors flat: {"error": {code, message}}."""
            return JSONResponse(
                status_code=exc.status_code,
                content=format_error_response(
                    exc.status_code, exc.message, _debug_detail_allowed(request)
                ),
                headers=_merge_error_headers(_get_cors_headers(request), None),
            )
    except ImportError:  # pragma: no cover
        pass
