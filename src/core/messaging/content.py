"""
Content validation and filtering for messages.

Handles:
- Input validation (SQL injection, XSS prevention)
- Profanity filtering
- NSFW content detection
- Spoiler tag processing
- Rich text formatting validation
"""

import re
from typing import Tuple, List, Optional, Dict, Any, Set
from dataclasses import dataclass

import utils.validator as validator
import utils.config as config

from .models import ContentFilter, FilterAction, TextFormat


@dataclass
class ContentValidationResult:
    """Result of content validation."""

    valid: bool
    sanitized_content: str
    issues: List[str]
    warnings: List[str]
    filtered_words: List[str]
    has_spoilers: bool
    has_nsfw: bool


# Default profanity word list (basic, can be extended via config)
DEFAULT_PROFANITY_WORDS = [
    # Keeping this minimal - real implementation would load from config/file
]

# NSFW indicators
NSFW_PATTERNS = [
    r"\bnsfw\b",
    r"\b18\+\b",
    r"\badult\s*content\b",
]


class ContentProcessor:
    """Processes and validates message content."""

    def __init__(self) -> None:
        self._profanity_words: Set[str] = set()
        self._nsfw_patterns: List[str] = []
        self._load_config()

    def _load_config(self) -> None:
        """Load content filtering configuration."""
        messaging_config = config.get("messaging", {})
        content_config = messaging_config.get("content", {})

        # Load profanity words
        custom_words = content_config.get("profanity_words", [])
        self._profanity_words = set(DEFAULT_PROFANITY_WORDS + custom_words)

        # Load NSFW patterns
        custom_nsfw = content_config.get("nsfw_patterns", [])
        self._nsfw_patterns = NSFW_PATTERNS + custom_nsfw

    def validate_content(
        self,
        content: str,
        user_filter: Optional[ContentFilter] = None,
        max_length: Optional[int] = None,
    ) -> ContentValidationResult:
        """
        Validate and process message content.

        Args:
            content: Raw message content
            user_filter: User's filter settings
            max_length: Maximum allowed length

        Returns:
            ContentValidationResult with validation status and processed content
        """
        issues: List[str] = []
        warnings: List[str] = []
        filtered_words: List[str] = []
        has_spoilers: bool = False
        has_nsfw: bool = False

        # Check for empty content
        if not content or not content.strip():
            return ContentValidationResult(
                valid=False,
                sanitized_content="",
                issues=["Content cannot be empty"],
                warnings=[],
                filtered_words=[],
                has_spoilers=False,
                has_nsfw=False,
            )

        # Check length
        if max_length and len(content) > max_length:
            issues.append(f"Content exceeds maximum length of {max_length} characters")
            return ContentValidationResult(
                valid=False,
                sanitized_content=content,
                issues=issues,
                warnings=[],
                filtered_words=[],
                has_spoilers=False,
                has_nsfw=False,
            )

        # Validate using common validator (SQL injection, XSS)
        validation_result = validator.validate(content)
        if not validation_result.is_valid:
            # Content contains potentially dangerous patterns
            # For messages, we sanitize rather than reject
            warnings.append(
                "Content contained potentially unsafe patterns and was sanitized"
            )

        sanitized = validation_result.sanitized_value or content

        # Check for spoiler tags
        has_spoilers = bool(re.search(TextFormat.PATTERNS["spoiler"], sanitized))

        # Check for NSFW indicators
        for pattern in self._nsfw_patterns:
            if re.search(pattern, sanitized, re.IGNORECASE):
                has_nsfw = True
                break

        # Apply user filters if provided
        if user_filter:
            sanitized, filtered = self._apply_user_filters(sanitized, user_filter)
            filtered_words.extend(filtered)

        return ContentValidationResult(
            valid=len(issues) == 0,
            sanitized_content=sanitized,
            issues=issues,
            warnings=warnings,
            filtered_words=filtered_words,
            has_spoilers=has_spoilers,
            has_nsfw=has_nsfw,
        )

    def _apply_user_filters(
        self, content: str, user_filter: ContentFilter
    ) -> Tuple[str, List[str]]:
        """
        Apply user-specific content filters.

        Args:
            content: Content to filter
            user_filter: User's filter settings

        Returns:
            Tuple of (filtered_content, list_of_filtered_words)
        """
        filtered_words: List[str] = []
        result: str = content

        # Build word list to filter
        words_to_filter: Set[str] = set()

        if user_filter.profanity_filter:
            words_to_filter.update(self._profanity_words)

        if user_filter.custom_blocked_words:
            words_to_filter.update(user_filter.custom_blocked_words)

        # Apply filtering based on action
        for word in words_to_filter:
            if not word:
                continue
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            if pattern.search(result):
                filtered_words.append(word)

                if user_filter.filter_action == FilterAction.CENSOR:
                    # Replace with asterisks
                    replacement = "*" * len(word)
                    result = pattern.sub(replacement, result)
                elif user_filter.filter_action == FilterAction.SPOILER:
                    # Wrap in spoiler tags
                    result = pattern.sub(f"||{word}||", result)
                # BLOCK and WARN don't modify content

        return result, filtered_words

    def parse_formatting(self, content: str) -> Dict[str, Any]:
        """
        Parse rich text formatting in content.

        Args:
            content: Message content

        Returns:
            Dict with formatting information
        """
        formatting: Dict[str, List[Dict[str, Any]]] = {
            "bold": [],
            "italic": [],
            "underline": [],
            "strikethrough": [],
            "spoiler": [],
            "code": [],
            "code_block": [],
            "quote": [],
        }

        for format_type, pattern in TextFormat.PATTERNS.items():
            flags = re.MULTILINE if format_type == "quote" else 0
            matches = re.finditer(pattern, content, flags)
            for match in matches:
                formatting[format_type].append(
                    {
                        "start": match.start(),
                        "end": match.end(),
                        "content": match.group(1) if match.groups() else match.group(0),
                    }
                )

        return formatting

    def strip_formatting(self, content: str) -> str:
        """
        Remove all formatting markers from content.

        Args:
            content: Formatted content

        Returns:
            Plain text content
        """
        result = content

        # Remove code blocks first (they may contain other markers)
        result = re.sub(TextFormat.PATTERNS["code_block"], r"\2", result)
        result = re.sub(TextFormat.PATTERNS["code"], r"\1", result)

        # Remove other formatting
        result = re.sub(TextFormat.PATTERNS["bold"], r"\1", result)
        result = re.sub(TextFormat.PATTERNS["italic"], r"\1", result)
        result = re.sub(TextFormat.PATTERNS["underline"], r"\1", result)
        result = re.sub(TextFormat.PATTERNS["strikethrough"], r"\1", result)
        result = re.sub(TextFormat.PATTERNS["spoiler"], r"\1", result)
        result = re.sub(TextFormat.PATTERNS["quote"], r"\1", result, flags=re.MULTILINE)

        return result

    def create_preview(self, content: str, max_length: int = 100) -> str:
        """
        Create a preview of message content.

        Args:
            content: Full message content
            max_length: Maximum preview length

        Returns:
            Truncated plain text preview
        """
        plain = self.strip_formatting(content)

        if len(plain) <= max_length:
            return plain

        # Truncate at word boundary
        truncated = plain[:max_length]
        last_space = truncated.rfind(" ")
        if last_space > max_length * 0.7:
            truncated = truncated[:last_space]

        return truncated + "..."


# Module-level instance
_processor: Optional[ContentProcessor] = None


def get_processor() -> ContentProcessor:
    """Get or create the content processor instance."""
    global _processor
    if _processor is None:
        _processor = ContentProcessor()
    return _processor


def validate_content(
    content: str,
    user_filter: Optional[ContentFilter] = None,
    max_length: Optional[int] = None,
) -> ContentValidationResult:
    """Validate message content."""
    return get_processor().validate_content(content, user_filter, max_length)


def parse_formatting(content: str) -> Dict[str, Any]:
    """Parse rich text formatting."""
    return get_processor().parse_formatting(content)


def strip_formatting(content: str) -> str:
    """Remove formatting markers."""
    return get_processor().strip_formatting(content)


def create_preview(content: str, max_length: int = 100) -> str:
    """Create content preview."""
    return get_processor().create_preview(content, max_length)
