"""
Regex pattern rule.

Checks messages against configurable regex patterns.
"""

import re
from typing import Dict, Any, Optional, List

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class RegexRule(BaseRule):
    """Rule that checks content against regex patterns."""

    rule_type = RuleType.REGEX

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._patterns: List[Dict[str, Any]] = self.config.get("patterns", [])
        self._compiled_patterns: List[tuple] = []

        for pattern_config in self._patterns:
            pattern_str = pattern_config.get("pattern", "")
            flags = 0
            if not pattern_config.get("case_sensitive", False):
                flags |= re.IGNORECASE
            if pattern_config.get("multiline", False):
                flags |= re.MULTILINE

            try:
                compiled = re.compile(pattern_str, flags)
                severity = self._parse_severity(
                    pattern_config.get("severity", "medium")
                )
                name = pattern_config.get("name", pattern_str[:30])
                self._compiled_patterns.append((compiled, severity, name))
            except re.error:
                pass

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check content against regex patterns."""
        if not self._compiled_patterns:
            return self._no_match()

        matches = []
        highest_severity = ViolationSeverity.LOW

        for compiled, severity, name in self._compiled_patterns:
            match = compiled.search(content)
            if match:
                matches.append(
                    {
                        "name": name,
                        "matched": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                    }
                )
                if self._severity_rank(severity) > self._severity_rank(
                    highest_severity
                ):
                    highest_severity = severity

        if not matches:
            return self._no_match()

        matched_texts = [m["matched"] for m in matches]

        return self._create_match(
            matched=True,
            matched_content=", ".join(matched_texts[:5]),
            details={"matches": matches, "count": len(matches)},
            severity=highest_severity,
        )

    def _parse_severity(self, sev_str: str) -> ViolationSeverity:
        """Parse severity string to enum."""
        mapping = {
            "low": ViolationSeverity.LOW,
            "medium": ViolationSeverity.MEDIUM,
            "high": ViolationSeverity.HIGH,
            "critical": ViolationSeverity.CRITICAL,
        }
        return mapping.get(sev_str.lower(), ViolationSeverity.MEDIUM)

    def _severity_rank(self, sev: ViolationSeverity) -> int:
        """Get numeric rank for severity comparison."""
        ranks = {
            ViolationSeverity.LOW: 1,
            ViolationSeverity.MEDIUM: 2,
            ViolationSeverity.HIGH: 3,
            ViolationSeverity.CRITICAL: 4,
        }
        return ranks.get(sev, 2)

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate regex rule configuration."""
        issues = []

        patterns = config.get("patterns")
        if not patterns:
            issues.append("patterns list is required")
        elif not isinstance(patterns, list):
            issues.append("patterns must be a list")
        else:
            for i, p in enumerate(patterns):
                if not isinstance(p, dict):
                    issues.append(f"pattern {i} must be a dictionary")
                    continue

                pattern_str = p.get("pattern")
                if not pattern_str:
                    issues.append(f"pattern {i} missing 'pattern' field")
                    continue

                try:
                    re.compile(pattern_str)
                    
                    # ReDoS vulnerability detection - check for potentially malicious patterns
                    # Look for nested quantifiers that can cause exponential backtracking
                    if re.search(r'\((?:[^()]*\([^()]*\))*[^()]*\)\*', pattern_str):
                        issues.append(f"pattern {i} contains potentially malicious nested quantifiers")
                    elif re.search(r'\([^()]*\)\{0,', pattern_str):
                        issues.append(f"pattern {i} contains potentially malicious quantifier combinations")
                    elif re.search(r'[a-zA-Z]*\*[a-zA-Z]*\*', pattern_str):
                        issues.append(f"pattern {i} contains potentially malicious repeated quantifiers")
                        
                except re.error as e:
                    issues.append(f"pattern {i} invalid regex: {e}")

        return len(issues) == 0, issues

