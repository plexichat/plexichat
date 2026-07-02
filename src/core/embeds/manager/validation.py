"""
Embed validation mixin - Re-exposes validation functions for test compatibility.
"""

from typing import Any, Dict

from src.core.embeds.validator import (
    sanitize_content as _sanitize_content,
    validate_embed_data as _validate_embed_data,
)


class EmbedValidationMixin:
    """
    Mixin that re-exposes validation functions for tests.

    The actual validation logic lives in ..validator module.
    This mixin provides method wrappers that return dicts for test compatibility.
    """

    _validate_embed_data: Any
    _sanitize_content: Any

    def validate_embed(self, embed_data: Dict[str, Any]) -> Dict[str, Any]:
        """Re-exposed validation for tests."""
        result = _validate_embed_data(embed_data)
        return {
            "valid": result.valid,
            "issues": result.issues,
            "total_chars": result.total_chars,
            "sanitized_data": result.sanitized_data,
        }

    def sanitize_embed_content(self, content: str) -> str:
        """Re-exposed sanitization for tests."""
        return _sanitize_content(content, "content")

    def validate_embed_data(self, embed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate embed data and return validation result.

        Args:
            embed_data: Embed data dictionary

        Returns:
            Dict with valid, issues, total_chars, sanitized_data
        """
        result = _validate_embed_data(embed_data)
        return {
            "valid": result.valid,
            "issues": result.issues,
            "total_chars": result.total_chars,
            "sanitized_data": result.sanitized_data,
        }

    def sanitize_content(self, content: str) -> str:
        """
        Sanitize embed content (remove scripts, validate URLs).

        Args:
            content: Content to sanitize

        Returns:
            Sanitized content
        """
        return _sanitize_content(content, "content")
