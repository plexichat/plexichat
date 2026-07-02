import re
import html
from typing import List, Optional, Pattern
from dataclasses import dataclass


@dataclass
class ValidationResult:
    is_valid: bool
    sanitized_value: Optional[str] = None
    error_message: Optional[str] = None


class Validator:
    """
    A utility for validating and sanitizing text data against common vulnerabilities.
    """

    # Common SQL injection patterns
    SQL_INJECTION_PATTERNS = [
        r"(?i)\bUNION\s+SELECT\b",
        r"(?i)\bSELECT\s+.*\s+FROM\b",
        r"(?i)\bINSERT\s+INTO\b",
        r"(?i)\bUPDATE\s+.*\s+SET\b",
        r"(?i)\bDELETE\s+FROM\b",
        r"(?i)\bDROP\s+TABLE\b",
        r"(?i)\s+OR\s+.*=.*",
        r"(?i)--",
        r"(?i);\s*$",
    ]

    # Common XSS patterns
    XSS_PATTERNS = [
        r"(?i)<script.*?>.*?</script.*?>",
        r"(?i)javascript:",
        r"(?i)on\w+\s*=",
        r"(?i)<iframe.*?>",
        r"(?i)<object.*?>",
    ]

    def __init__(
        self,
        blocklist_patterns: Optional[List[str]] = None,
        escape_char: str = '"',
        allow_escaped: bool = False,
        auto_sanitize_html: bool = True,
    ):
        """
        Initialize the Validator.

        Args:
            blocklist_patterns (list): Regex patterns to check for.
            escape_char (str): Character used to escape dangerous content.
            allow_escaped (bool): If True, allows blocked patterns if they are enclosed in escape_char.
                                WARNING: Setting this to True can be dangerous.
            auto_sanitize_html (bool): If True, automatically escapes HTML tags.
        """
        self.blocklist_patterns = blocklist_patterns or (
            self.SQL_INJECTION_PATTERNS + self.XSS_PATTERNS
        )
        self.compiled_patterns: List[Pattern[str]] = [
            re.compile(p, re.IGNORECASE | re.DOTALL) for p in self.blocklist_patterns
        ]
        self.escape_char = escape_char
        self.allow_escaped = allow_escaped
        self.auto_sanitize_html = auto_sanitize_html

    def validate(self, text: Optional[str]) -> ValidationResult:
        """
        Validates the input text.

        Args:
            text (str): The text to validate.

        Returns:
            ValidationResult: Contains status, sanitized value (if applicable), and error.
        """
        if not text:
            return ValidationResult(True, text)

        # 1. Blocklist Pattern Matching
        for pattern in self.compiled_patterns:
            match = pattern.search(text)
            if match:
                # Found a blocked pattern
                return ValidationResult(
                    False, None, f"Detected blocked pattern: {pattern.pattern}"
                )

        sanitized = text

        # 2. HTML Sanitization
        if self.auto_sanitize_html:
            sanitized = html.escape(sanitized)

        return ValidationResult(True, sanitized)

    def add_pattern(self, pattern: str) -> None:
        """Adds a new regex pattern to the blocklist."""
        self.compiled_patterns.append(re.compile(pattern, re.IGNORECASE | re.DOTALL))
