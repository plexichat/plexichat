"""Typed errors for the aiortc SFU adapter."""

from __future__ import annotations

from ...exceptions import SFUConnectionError


class SFUValidationError(SFUConnectionError):
    """Raised when SFU input fails validation (fail-closed)."""


__all__ = ["SFUConnectionError", "SFUValidationError"]
