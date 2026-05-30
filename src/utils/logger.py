"""Proxy module - re-exports logger from common_utils for static analysis."""

from .common_utils.utils.logger import *  # noqa: F403
from .common_utils.utils.logger import (  # noqa: F401
    _logger_instance,
    _setup_called,
)
