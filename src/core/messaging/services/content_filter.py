"""
Content filter service - Business logic for content filtering.
"""

from typing import Any, List, Optional, Set
import re

from ..models import ContentFilter, FilterAction
from ..repositories.user_settings import UserSettingsRepository
from ..content import ContentValidationResult
from .base import BaseService
from src.core.base import SnowflakeID
import utils.validator as validator


# Default profanity word list (minimal - extend via config)
DEFAULT_PROFANITY_WORDS: List[str] = []

# NSFW indicators
NSFW_PATTERNS = [
    r"\bnsfw\b",
    r"\b18\+\b",
    r"\badult\s*content\b",
]


class ContentFilterService(BaseService):
    """Service for content filtering operations."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self._repo = UserSettingsRepository(db)
        self._profanity_words: Set[str] = set()
        self._nsfw_patterns: List[re.Pattern[str]] = []
        self._profanity_pattern: Optional[re.Pattern[str]] = None
        self._load_config()

    def _load_config(self) -> None:
        """Load content filtering configuration."""
        content_config = self._config.get("content", {})

        # Load profanity words
        custom_words = content_config.get("profanity_words", [])
        self._profanity_words = set(DEFAULT_PROFANITY_WORDS + custom_words)

        # Pre-compile profanity pattern for O(N) instead of O(N*M) filtering
        if self._profanity_words:
            escaped_words = [re.escape(w) for w in self._profanity_words if w]
            if escaped_words:
                self._profanity_pattern = re.compile(
                    r"\b(" + "|".join(escaped_words) + r")\b",
                    re.IGNORECASE,
                )

        # Pre-compile NSFW patterns
        custom_nsfw = content_config.get("nsfw_patterns", [])
        all_nsfw = NSFW_PATTERNS + custom_nsfw
        self._nsfw_patterns = [re.compile(p, re.IGNORECASE) for p in all_nsfw]

    def get_filter_settings(self, user_id: SnowflakeID) -> ContentFilter:
        """Get user's content filter settings (cached)."""
        cache_key = ("filter_settings", user_id)

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        row = self._repo.get_filter_settings(user_id)

        if row:
            result = self._repo.row_to_filter_settings(row)
        else:
            result = ContentFilter(user_id=user_id)

        self._cache_set(cache_key, result)
        return result

    def update_filter_settings(
        self,
        user_id: SnowflakeID,
        profanity_filter: Optional[bool] = None,
        nsfw_filter: Optional[bool] = None,
        spoiler_click_to_reveal: Optional[bool] = None,
        custom_blocked_words: Optional[List[str]] = None,
        filter_action: Optional[FilterAction] = None,
    ) -> ContentFilter:
        """Update user's content filter settings."""
        current = self.get_filter_settings(user_id)

        new_profanity = profanity_filter if profanity_filter is not None else current.profanity_filter
        new_nsfw = nsfw_filter if nsfw_filter is not None else current.nsfw_filter
        new_spoiler = spoiler_click_to_reveal if spoiler_click_to_reveal is not None else current.spoiler_click_to_reveal
        new_words = custom_blocked_words if custom_blocked_words is not None else current.custom_blocked_words
        new_action = filter_action if filter_action is not None else current.filter_action

        if self._repo.filter_settings_exists(user_id):
            self._repo.update_filter_settings(
                user_id,
                new_profanity,
                new_nsfw,
                new_spoiler,
                new_words,
                new_action,
            )
        else:
            self._repo.create_filter_settings(
                user_id,
                new_profanity,
                new_nsfw,
                new_spoiler,
                new_words,
                new_action,
            )

        # Invalidate cache
        self._cache_invalidate(("filter_settings", user_id))

        return self.get_filter_settings(user_id)

    def validate_content(
        self,
        content: str,
        user_filter: Optional[ContentFilter] = None,
        max_length: Optional[int] = None,
    ) -> ContentValidationResult:
        """
        Validate and process message content.

        Uses optimized single-pass regex for profanity filtering.
        """
        issues: List[str] = []
        warnings: List[str] = []
        filtered_words: List[str] = []
        has_spoilers = False
        has_nsfw = False

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
            warnings.append("Content contained potentially unsafe patterns and was sanitized")

        sanitized = validation_result.sanitized_value or content

        # Check for spoiler tags (single regex check)
        has_spoilers = bool(re.search(r"\|\|.+?\|\|", sanitized))

        # Check for NSFW indicators (pre-compiled patterns)
        for pattern in self._nsfw_patterns:
            if pattern.search(sanitized):
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
    ) -> tuple[str, List[str]]:
        """
        Apply user-specific content filters.

        Uses optimized single-pass regex matching.
        """
        filtered_words: List[str] = []
        result = content

        # Build combined pattern for all words to filter
        words_to_filter: Set[str] = set()

        if user_filter.profanity_filter:
            words_to_filter.update(self._profanity_words)

        if user_filter.custom_blocked_words:
            words_to_filter.update(user_filter.custom_blocked_words)

        if not words_to_filter:
            return result, filtered_words

        # Build single pattern for all words (O(N) instead of O(N*M))
        escaped_words = [re.escape(w) for w in words_to_filter if w]
        if not escaped_words:
            return result, filtered_words

        combined_pattern = re.compile(
            r"\b(" + "|".join(escaped_words) + r")\b",
            re.IGNORECASE,
        )

        # Find all matches first
        matches = combined_pattern.findall(result)
        filtered_words.extend(set(m.lower() for m in matches))

        # Apply filtering based on action
        if user_filter.filter_action == FilterAction.CENSOR:
            result = combined_pattern.sub(lambda m: "*" * len(m.group(0)), result)
        elif user_filter.filter_action == FilterAction.SPOILER:
            result = combined_pattern.sub(lambda m: f"||{m.group(0)}||", result)
        # BLOCK and WARN don't modify content

        return result, filtered_words
