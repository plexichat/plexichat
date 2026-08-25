"""
Typed (isinstance-based) mapping of core-module exceptions to HTTP errors.

Replaces the brittle ``type(e).__name__`` substring sniffing previously
scattered across the channels/invites/webhooks/attachments routes: a
differently-named but semantically identical error (e.g. a permission
error whose class happens to contain "Member") could be mapped to the
wrong status code, and authorization failures degraded to generic 500s
with raw ``str(e)`` leaked in the body. Mapping is now class-based and
response messages are static.
"""

from typing import NoReturn

from fastapi import HTTPException

from src.core.servers import exceptions as srv
from src.core.webhooks import exceptions as wh


def _http(status_code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": status_code, "message": message}},
    )


# 404 - resource does not exist (or invite no longer resolvable)
_NOT_FOUND_EXC = (
    srv.ServerNotFoundError,
    srv.ChannelNotFoundError,
    srv.CategoryNotFoundError,
    srv.RoleNotFoundError,
    srv.MemberNotFoundError,
    srv.InviteNotFoundError,
    srv.InviteExpiredError,
    srv.TemplateNotFoundError,
    srv.ScheduledEventNotFoundError,
    srv.BanNotFoundError,
    srv.WelcomeScreenNotFoundError,
    srv.OnboardingStepNotFoundError,
    wh.WebhookNotFoundError,
    wh.ChannelNotFoundError,
)

# 403 - exists, but the caller is not allowed
_PERMISSION_EXC = (
    srv.ServerAccessDeniedError,
    srv.ChannelAccessDeniedError,
    srv.RoleAccessDeniedError,
    srv.PermissionDeniedError,
    srv.UserBannedError,
    srv.InviteMaxUsesError,
    wh.WebhookAccessDeniedError,
    wh.PermissionDeniedError,
)

# 400 - invalid client input / validation failure
_INVALID_EXC = (
    srv.InvalidServerNameError,
    srv.InvalidChannelNameError,
    srv.InvalidRoleNameError,
    srv.InvalidEventTimeError,
    srv.InvalidPermissionKeyError,
    srv.InvalidTemplateCodeError,
    srv.ChannelTypeError,
    srv.RoleHierarchyError,
    wh.WebhookNameError,
    wh.WebhookAvatarError,
    wh.InvalidContentError,
    wh.EmbedLimitError,
    wh.WebhookLimitError,
)

# 429 - throttled. The webhook execution window is 60s (see
# WebhookExecutionMixin.execute_webhook), so that is the honest
# Retry-After hint for clients.
RATE_LIMIT_RETRY_AFTER_SECONDS = 60


def is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, wh.RateLimitError)


def is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, _NOT_FOUND_EXC)


def is_permission_denied(exc: BaseException) -> bool:
    return isinstance(exc, _PERMISSION_EXC)


def is_invalid_request(exc: BaseException) -> bool:
    return isinstance(exc, _INVALID_EXC)


def raise_mapped(
    exc: BaseException,
    *,
    not_found_message: str = "Not found",
    permission_message: str = "Access denied",
    invalid_message: str = "Invalid request",
) -> NoReturn:
    """
    Re-raise ``exc`` as the mapped HTTPException.

    Falls through (re-raises unchanged) when the exception is not one of
    the recognized core-module types so unexpected errors keep reaching
    the caller's generic 500 handler with full logging context.
    """
    if is_permission_denied(exc):
        raise _http(403, permission_message)
    if is_not_found(exc):
        raise _http(404, not_found_message)
    if is_rate_limited(exc):
        # 429 with Retry-After semantics so clients can back off instead
        # of treating the throttle as a server fault (previous behavior
        # surfaced a generic 500).
        raise HTTPException(
            status_code=429,
            detail={"error": {"code": 429, "message": "Rate limit exceeded"}},
            headers={"Retry-After": str(RATE_LIMIT_RETRY_AFTER_SECONDS)},
        )
    if is_invalid_request(exc):
        raise _http(400, invalid_message)
    raise exc


def map_invite_join_error(exc: BaseException) -> NoReturn:
    """Mapping for POST /channels/invites/{code} (join-server flow)."""
    from src.core.servers.exceptions import MemberExistsError

    if isinstance(exc, MemberExistsError):
        raise _http(409, "Already a member of this server")
    if isinstance(exc, srv.UserBannedError):
        raise _http(403, "You are banned from this server")
    if isinstance(exc, srv.InviteMaxUsesError):
        raise _http(400, "Invite is no longer usable")
    raise_mapped(
        exc,
        not_found_message="Invite not found or expired",
        permission_message="Access denied",
        invalid_message="Invite not found or expired",
    )
