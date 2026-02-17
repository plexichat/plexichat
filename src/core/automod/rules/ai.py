from typing import Dict, Any, Optional, List

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class AIModerationRule(BaseRule):
    rule_type = RuleType.AI_MODERATION

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._backend: str = self.config.get("backend", "openai")
        self._categories: Optional[List[str]] = self.config.get("categories")
        self._score_threshold = self.config.get("score_threshold")
        self._severity = self._parse_severity(self.config.get("severity"))

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        context = context or {}
        manager = context.get("automod_manager")
        if not manager:
            return self._no_match()

        try:
            result = manager.check_ai(
                content=content, backend=self._backend, context=context
            )
        except Exception as exc:
            return self._create_match(
                matched=False,
                details={"backend": self._backend, "error": str(exc)},
                severity=self._severity,
            )

        matched_categories = [
            name for name, flagged in (result.categories or {}).items() if flagged
        ]

        if self._score_threshold is not None:
            for name, score in (result.scores or {}).items():
                if score >= self._score_threshold and name not in matched_categories:
                    matched_categories.append(name)

        if self._categories:
            matched_categories = [
                name for name in matched_categories if name in self._categories
            ]

        matched = bool(matched_categories) or bool(result.flagged)
        if self._categories:
            matched = bool(matched_categories)

        if not matched:
            return self._no_match()

        details: Dict[str, Any] = {
            "backend": result.backend.value
            if hasattr(result.backend, "value")
            else str(result.backend),
            "flagged": result.flagged,
            "categories": result.categories,
            "scores": result.scores,
            "matched_categories": matched_categories,
        }

        if self._score_threshold is not None:
            details["score_threshold"] = self._score_threshold

        matched_content = ", ".join(matched_categories) if matched_categories else "flagged"

        return self._create_match(
            matched=True,
            matched_content=matched_content,
            details=details,
            severity=self._severity,
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        issues = []
        backend = config.get("backend")
        if backend and backend not in {"openai", "perspective", "custom"}:
            issues.append("backend must be one of: openai, perspective, custom")

        categories = config.get("categories")
        if categories is not None and not isinstance(categories, list):
            issues.append("categories must be a list")

        score_threshold = config.get("score_threshold")
        if score_threshold is not None and not isinstance(
            score_threshold, (int, float)
        ):
            issues.append("score_threshold must be a number")

        severity = config.get("severity")
        if severity is not None:
            try:
                cls._parse_severity(severity)
            except Exception:
                issues.append("severity must be a valid ViolationSeverity value")

        return len(issues) == 0, issues

    @staticmethod
    def _parse_severity(value: Any) -> ViolationSeverity:
        if isinstance(value, ViolationSeverity):
            return value
        if isinstance(value, str):
            return ViolationSeverity(value)
        return ViolationSeverity.MEDIUM
