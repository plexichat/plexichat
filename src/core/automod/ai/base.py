"""
Base AI adapter class - Abstract base for AI moderation backends.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..models import AICheckResult


class BaseAIAdapter(ABC):
    """Abstract base class for AI moderation adapters."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the AI adapter.
        
        Args:
            config: Configuration dictionary with API keys and settings
        """
        self._config = config
        self._timeout = config.get("timeout_seconds", 10)
    
    @abstractmethod
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
        """
        Check content using the AI moderation backend.
        
        Args:
            content: Text content to check
            context: Optional additional context
            
        Returns:
            AICheckResult with moderation results
        """
        pass
    
    @classmethod
    @abstractmethod
    def get_backend_name(cls) -> str:
        """Get the backend identifier name."""
        pass
    
    @classmethod
    @abstractmethod
    def validate_config(cls, config: Dict[str, Any]) -> list:
        """
        Validate adapter configuration.
        
        Args:
            config: Configuration to validate
            
        Returns:
            List of validation error messages
        """
        pass
    
    def is_configured(self) -> bool:
        """Check if adapter is properly configured."""
        return len(self.validate_config(self._config)) == 0
