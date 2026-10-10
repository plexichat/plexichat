"""PlexiSuite route groups."""

from .registry import router as registry_router
from .stash import router as stash_router
from .sum import router as sum_router

__all__ = ["registry_router", "stash_router", "sum_router"]
