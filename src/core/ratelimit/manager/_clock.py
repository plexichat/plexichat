"""Clock indirection for the rate-limit manager.

The manager must use the clock patched onto the ``manager`` package by the
``rate_limit_clock`` test fixture. Reading ``time`` through the package
attribute on every call keeps that fixture working while allowing the
implementation to live in submodules.
"""

import sys
import time as _stdlib_time
from typing import Protocol


class _Clock(Protocol):
    def time(self) -> float: ...


def _package_clock() -> _Clock:
    """Return the clock currently attached to the manager package."""
    package = sys.modules.get(__package__ or "")
    if package is None:
        return _stdlib_time
    return getattr(package, "time", _stdlib_time)


def now() -> float:
    """Return the current wall-clock timestamp as a float."""
    return float(_package_clock().time())
