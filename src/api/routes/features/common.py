import re
from typing import NoReturn

from fastapi import HTTPException

import utils.logger as logger
from src.core.security.hardening import (
    HardeningViolation,
    parse_id as _parse_snowflake_id,
)

# Feature-route ids are written as plain ASCII decimal digits in paths and
# bodies. Keep that strict grammar here (the shared bounded_int also tolerates
# a leading sign) so delegating the range check never loosens what is accepted.
_ASCII_DIGITS = re.compile(r"[0-9]+")


def raise_bad_request(message: str) -> NoReturn:
    raise HTTPException(
        status_code=400, detail={"error": {"code": 400, "message": message}}
    )


def raise_forbidden(message: str) -> NoReturn:
    raise HTTPException(
        status_code=403, detail={"error": {"code": 403, "message": message}}
    )


def raise_not_found(message: str) -> NoReturn:
    raise HTTPException(
        status_code=404, detail={"error": {"code": 404, "message": message}}
    )


def raise_internal(message: str = "Internal server error") -> NoReturn:
    raise HTTPException(
        status_code=500, detail={"error": {"code": 500, "message": message}}
    )


def resolve_participant_service(messaging):
    """Return the messaging module's live ParticipantService, or None.

    The module object exposes its ``MessagingManager`` as ``_manager`` (and some
    builds as ``_message_svc``); the real ``ParticipantService`` is the manager's
    ``_participant_svc`` attribute -- *not* an attribute of the module itself.
    Feature routes historically used ``getattr(messaging, "_participant_svc",
    None)``, which is always ``None`` in a healthy app. That silently disabled
    the participation checks in the messaging services (fail-open). Those
    services now fail CLOSED when the checker is missing, so routes must resolve
    the real service here or every legitimate request is denied with 403.

    Returns None only when messaging is genuinely unavailable; callers fail
    closed in that case (which is the intended behavior).
    """
    if messaging is None:
        return None
    manager = getattr(messaging, "_manager", None)
    if manager is None:
        manager = getattr(messaging, "_message_svc", messaging)
    svc = getattr(manager, "_participant_svc", None)
    if svc is None:
        # Fall back to the module-level is_participant wrapper if this build
        # does not expose the manager's service directly.
        if callable(getattr(messaging, "is_participant", None)):
            return messaging
    return svc


def parse_id(value: str, field: str = "ID") -> int:
    """Parse a positive, 64-bit-bounded decimal identifier.

    This used to be a local ``str.isdecimal()`` + ``int()`` check with no
    ceiling: ``99999999999999999999999`` parsed to an out-of-range int that
    later reached SQL bindings (where SQLite raises ``OverflowError`` and the
    route degrades to a 500) and offset arithmetic. ``str.isdecimal()`` also
    accepts Unicode digit homoglyphs (``١٢٣``), so one hop could read a
    different id than the next. Delegate to the shared snowflake guard so
    every feature route enforces the same 1 .. 2**63-1 range, ASCII-only
    grammar and digit-count cap. The route's 400 contract is preserved by
    translating the guard's rejection into the usual bad-request response.
    """
    if isinstance(value, bool):
        raise_bad_request(f"Invalid {field}")
    text = str(value).strip()
    if not _ASCII_DIGITS.fullmatch(text):
        raise_bad_request(f"Invalid {field}")
    try:
        return _parse_snowflake_id(text, field)
    except HardeningViolation:
        raise_bad_request(f"Invalid {field}")


def call_or_raise(func, *args, **kwargs):
    """Invoke a service and expose only client-safe exception messages."""
    try:
        return func(*args, **kwargs)
    except ValueError as exc:
        logger.debug(
            "Feature validation failed in %s: %s",
            getattr(func, "__name__", "service"),
            exc,
        )
        raise_bad_request("Invalid request")
    except PermissionError as exc:
        logger.debug(
            "Feature authorization failed in %s: %s",
            getattr(func, "__name__", "service"),
            exc,
        )
        if "participant" in str(exc).lower():
            raise_forbidden("Not a participant in this conversation")
        raise_forbidden("Access denied")
