from typing import NoReturn

from fastapi import HTTPException

import utils.logger as logger


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


def parse_id(value: str, field: str = "ID") -> int:
    """Parse a positive decimal identifier without accepting coercion traps."""
    if isinstance(value, bool):
        raise_bad_request(f"Invalid {field}")
    text = str(value).strip()
    if not text.isdecimal():
        raise_bad_request(f"Invalid {field}")
    try:
        parsed = int(text)
    except (TypeError, ValueError, OverflowError):
        raise_bad_request(f"Invalid {field}")
    if parsed <= 0:
        raise_bad_request(f"Invalid {field}")
    return parsed


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
