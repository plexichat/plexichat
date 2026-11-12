"""Shared capability checks and error translation for the PlexiSum routes."""

import sys
from typing import Any

import src.api as api
from fastapi import HTTPException, status

from src.core.suite import sum as sum_domain
from src.core.suite.sum.manager import (
    SumConflictError,
    SumManager,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)


def _package() -> Any:
    """Return the package namespace the tests monkeypatch."""
    package = __package__
    if package is None:
        raise RuntimeError("suite route helpers are not inside a package")
    return sys.modules[package]


def _require_sum_capability() -> None:
    """Fail closed when PlexiSum is disabled and not in evaluation mode."""
    # Resolved through the package so ``monkeypatch.setattr`` on
    # ``src.api.routes.suite.sum.get_capability`` keeps working after the
    # module split.
    pkg = _package()
    info = pkg.get_capability("plexisum")
    if not pkg.is_usable(info.state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": 403,
                    "message": "PlexiSum is unavailable",
                    "state": info.state.value,
                }
            },
        )


def _manager() -> SumManager:
    _require_sum_capability()
    try:
        return sum_domain.get_manager()
    except RuntimeError:
        # Keep isolated route tests and embedded callers functional when they
        # provide a database without going through the full bootstrap path.
        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        # Keep the fixture/embedded fallback on the same compatibility path as
        # normal startup; constructing SumManager directly is not enough for
        # already-applied databases missing additive metadata tables/columns.
        return sum_domain.setup(
            db,
            servers_module=api.get_servers(),
            media=api.get_media(),
        )


def resolve_manager() -> SumManager:
    """Resolve ``_manager`` through the package namespace.

    Tests patch ``src.api.routes.suite.sum._manager``; looking the name up per
    call keeps that monkeypatch point effective now that the handlers live in
    their own modules.
    """
    return _package()._manager()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, SumNotFoundError):
        return HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(exc, SumPermissionError):
        return HTTPException(
            status_code=403, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(exc, SumConflictError):
        return HTTPException(
            status_code=409, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(exc, SumValidationError):
        return HTTPException(
            status_code=400, detail={"error": {"code": 404, "message": "Not found"}}
        )
    # Unknown failures must never vanish silently: log the full traceback so
    # a bare 500 is always diagnosable from latest.log.
    import utils.logger as logger

    logger.error(f"PlexiSum operation failed: {exc}", exc_info=True)
    return HTTPException(status_code=500, detail="PlexiSum operation failed")
