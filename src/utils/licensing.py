"""Proxy module - re-exports licensing from common_utils for static analysis."""

from .common_utils.utils.licensing import *  # noqa: F403
from .common_utils.utils.licensing import (  # noqa: F401
    _license_manager,
    _setup_called,
    _free_tier_mode,
    _public_key_configured,
)
