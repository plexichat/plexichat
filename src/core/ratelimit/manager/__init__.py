"""Rate limit manager package.

This package replaces the former single-file ``manager.py``. The public
surface is intentionally unchanged: importing :class:`RateLimitManager`
from ``src.core.ratelimit.manager`` keeps working for every existing
caller, while the internals are split into focused modules.

``time`` is re-exported here on purpose. The shared ``rate_limit_clock``
test fixture monkey-patches ``src.core.ratelimit.manager.time`` to drive
window/refill assertions deterministically; :mod:`._clock` resolves the
clock through this package attribute at call time, so the fixture keeps
working without patching every submodule.
"""

import time

from .core import RateLimitManager

__all__ = ["RateLimitManager", "time"]
