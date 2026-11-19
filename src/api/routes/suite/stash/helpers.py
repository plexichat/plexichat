"""Shared capability checks and error translation for the PlexiStash routes."""

import sys
from typing import Any

import src.api as api
from fastapi import HTTPException, status

from src.core.security.hardening import HardeningViolation, parse_id
from src.core.suite import stash as stash_domain
from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)


def parse_stash_id(raw: object, field: str = "id") -> int:
    """Parse a Stash boundary id through the shared snowflake id gate.

    The Stash route handlers used bare ``int(raw)`` on path/query ids. ``int``
    accepts Unicode decimal homoglyphs ("١٢٣", "１２３"), PEP 515 underscores
    ("1_2"), signs and arbitrarily large magnitudes, while every other route
    boundary funnels ids through the ASCII-only, 64-bit bounded ``parse_id``
    gate. That is a parser differential: a homoglyph can be read as a different
    id by an upstream ASCII-only validator (or another language's digit
    matcher), and an
    oversized value reaches SQL bindings as an OverflowError-inducing 500
    instead of a clean 400.

    Route all Stash ids through the same guard. ``StashValidationError`` is a
    ``ValueError`` subclass, so every handler's existing ``except ValueError``
    / ``_error`` mapping keeps returning its documented 400 response.
    """
    try:
        return parse_id(raw, field)
    except HardeningViolation as exc:
        raise StashValidationError(f"Invalid Stash {field}") from exc


def _package() -> Any:
    """Return the package namespace the tests monkeypatch."""
    package = __package__
    if package is None:
        raise RuntimeError("suite route helpers are not inside a package")
    return sys.modules[package]


def _require_stash_capability() -> None:
    """Fail closed when PlexiStash is disabled and not in evaluation mode."""
    # Resolved through the package so ``monkeypatch.setattr`` on
    # ``src.api.routes.suite.stash.get_capability`` keeps working after
    # the module split.
    pkg = _package()
    info = pkg.get_capability("plexistash")
    if not pkg.is_usable(info.state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": 403,
                    "message": "PlexiStash is unavailable",
                    "state": info.state.value,
                }
            },
        )


def _manager() -> StashManager:
    _require_stash_capability()
    try:
        return stash_domain.get_manager()
    except RuntimeError:
        # Keep isolated route tests and embedded callers functional when they
        # provide a database without going through the full bootstrap path.
        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        return StashManager(db, servers_module=api.get_servers(), media=api.get_media())


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, StashNotFoundError):
        return HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(exc, StashPermissionError):
        return HTTPException(
            status_code=403, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(exc, StashValidationError):
        return HTTPException(
            status_code=400, detail={"error": {"code": 404, "message": "Not found"}}
        )
    # Unknown failures must never vanish silently: log the full traceback so
    # a bare 500 is always diagnosable from latest.log.
    import utils.logger as logger

    logger.error(f"PlexiStash operation failed: {exc}", exc_info=True)
    return HTTPException(status_code=500, detail="PlexiStash operation failed")


def resolve_manager() -> StashManager:
    """Resolve ``_manager`` through the package namespace.

    Tests patch ``src.api.routes.suite.stash._manager``; looking the name
    up per call keeps that monkeypatch point effective now that the
    handlers live in their own modules.
    """
    return _package()._manager()
