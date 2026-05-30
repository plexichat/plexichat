"""
Validator utility module - Zero-friction data validation for Python applications.

Usage:
    # In main.py (optional setup for custom patterns)
    import utils.validator as validator
    validator.setup(allow_escaped=True)

    # In any other file (no setup needed - uses defaults)
    import utils.validator as validator
    result = validator.validate(user_input)
    if result.is_valid:
        # Process data
        pass
"""

from typing import List, Optional
from .core import Validator, ValidationResult

# Global validator instance
_validator_instance: Optional[Validator] = None
_setup_called = False


def setup(
    blocklist_patterns: Optional[List[str]] = None,
    escape_char: str = '"',
    allow_escaped: bool = True,
    auto_sanitize_html: bool = True,
) -> None:
    """
    Setup the validator with custom configuration. This is optional -
    validator will use sensible defaults if not called.

    Args:
        blocklist_patterns (list): Regex patterns to check for (e.g., SQL injection).
        escape_char (str): Character used to escape dangerous content.
        allow_escaped (bool): If True, allows blocked patterns if enclosed in escape_char.
        auto_sanitize_html (bool): If True, automatically escapes HTML tags.
    """
    global _validator_instance, _setup_called

    _validator_instance = Validator(
        blocklist_patterns=blocklist_patterns,
        escape_char=escape_char,
        allow_escaped=allow_escaped,
        auto_sanitize_html=auto_sanitize_html,
    )
    _setup_called = True


def _get_validator() -> Validator:
    """Internal: Get or create the validator instance."""
    global _validator_instance, _setup_called

    if not _setup_called:
        # Auto-initialize with defaults if setup wasn't called
        _validator_instance = Validator()
        _setup_called = True

    assert _validator_instance is not None
    return _validator_instance


def validate(text: str) -> ValidationResult:
    """
    Validate input text against configured patterns.

    Args:
        text (str): The text to validate.

    Returns:
        ValidationResult: Contains is_valid, sanitized_value, and error_message.
    """
    return _get_validator().validate(text)


def add_pattern(pattern: str) -> None:
    """
    Add a new regex pattern to the blocklist.

    Args:
        pattern (str): Regex pattern to add.
    """
    _get_validator().add_pattern(pattern)


# For backward compatibility, also expose the Validator class and ValidationResult
__all__ = ["Validator", "ValidationResult", "setup", "validate", "add_pattern"]
