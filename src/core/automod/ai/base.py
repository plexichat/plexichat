"""
Base AI adapter class.

All AI moderation backends inherit from BaseAIAdapter.
"""

import math
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..models import AICheckResult, AIBackendType

# Score thresholds and HTTP timeouts arrive from the admin AutoMod config (a
# free-form JSON dict) and are used directly in ``score >= threshold`` and
# ``urlopen(timeout=...)``. An unbounded value is not harmless config:
#   * NaN / inf threshold -> the comparison is always False -> score-based
#     moderation is silently disabled (fail open).
#   * negative / zero threshold -> every score matches -> a flood of false
#     violations (and moderation actions) on ordinary messages.
#   * an absurd timeout -> every moderated message pins a worker thread (or
#     overflows socket.settimeout), stalling/breaking the check pipeline.
# Coerce at the point the config is loaded into an adapter so every backend
# inherits the bound regardless of which write path supplied the value.
DEFAULT_THRESHOLD = 0.5
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 300.0


def _bounded_threshold(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_THRESHOLD
    value = float(value)
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        return DEFAULT_THRESHOLD
    return value


def _bounded_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_TIMEOUT_SECONDS
    value = float(value)
    if not math.isfinite(value) or not 0.0 < value <= MAX_TIMEOUT_SECONDS:
        return DEFAULT_TIMEOUT_SECONDS
    return value


class BaseAIAdapter(ABC):
    """Abstract base class for AI moderation backends."""

    backend_type: Optional[AIBackendType] = None

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the AI adapter.

        Args:
            config: Backend-specific configuration
        """
        self._config = config
        self._timeout = config.get("timeout_seconds", 10)

    # Subclasses assign ``self._threshold = config.get("threshold", ...)`` and
    # ``self._timeout`` in their own __init__; a data descriptor on the base
    # intercepts those assignments and bounds them for every backend.
    @property
    def _threshold(self) -> float:
        return self.__threshold

    @_threshold.setter
    def _threshold(self, value: Any) -> None:
        self.__threshold = _bounded_threshold(value)

    @property
    def _timeout(self) -> float:
        return self.__timeout

    @_timeout.setter
    def _timeout(self, value: Any) -> None:
        self.__timeout = _bounded_timeout(value)

    @abstractmethod
    def check_content(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> AICheckResult:
        """
        Check content using the AI backend.

        Args:
            content: Text content to check
            context: Additional context (user info, etc.)

        Returns:
            AICheckResult with moderation results
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if the backend is properly configured and available.

        Returns:
            True if backend can be used
        """
        pass

    def get_categories(self) -> Dict[str, str]:
        """
        Get supported moderation categories.

        Returns:
            Dict mapping category ID to description
        """
        return {}
