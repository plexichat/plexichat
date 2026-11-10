"""Bypass decisions.

Security rule: any failure while evaluating a bypass hook fails **closed**.
A broken custom hook or an unreadable config must never silently grant an
unlimited request.
"""

from typing import Any, Callable, Optional

import utils.logger as logger

from ..config import should_bypass_admin, should_bypass_internal

BypassCheck = Callable[[Optional[int], bool, bool], bool]


def _warning(message: str, *args: object, **kwargs: Any) -> None:
    try:
        logger.warning(message, *args, **kwargs)
    except Exception:
        # Logging must never turn a limiter decision into a 500.
        pass


def admin_bypass_enabled() -> bool:
    """Config-gated admin bypass; fail closed if config cannot be read."""
    try:
        return bool(should_bypass_admin())
    except Exception:
        _warning("ratelimit: could not read admin_bypass config; failing closed")
        return False


def internal_bypass_enabled() -> bool:
    """Config-gated internal bypass; fail closed if config cannot be read."""
    try:
        return bool(should_bypass_internal())
    except Exception:
        _warning("ratelimit: could not read internal_bypass config; failing closed")
        return False


def should_bypass(
    bypass_check: Optional[BypassCheck],
    user_id: Optional[int],
    is_admin: bool,
    is_internal: bool,
) -> bool:
    """Decide whether a request bypasses rate limiting."""
    if is_internal and internal_bypass_enabled():
        return True

    if bypass_check is not None:
        try:
            return bool(bypass_check(user_id, is_admin, is_internal))
        except Exception:
            _warning(
                "ratelimit: bypass_check raised; failing closed instead of bypassing",
                exc_info=True,
            )
            return False

    if is_admin and admin_bypass_enabled():
        return True

    return False
