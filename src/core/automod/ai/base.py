"""
Base AI adapter class.

All AI moderation backends inherit from BaseAIAdapter.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..models import AICheckResult, AIBackendType


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

    @abstractmethod
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
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
