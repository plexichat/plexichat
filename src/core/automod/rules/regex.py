"""
Regex pattern rule.

Checks messages against configurable regex patterns.
Uses google-re2 for safe, ReDoS-resistant pattern matching.
"""

import re
import utils.logger as logger
from typing import Dict, Any, Optional, List

# Try to use google-re2 for ReDoS protection, fallback to standard re
try:
    import re2
    HAS_RE2 = True
except ImportError:
    HAS_RE2 = False
    logger.warning("google-re2 not installed, falling back to standard 're' module. BEWARE OF REDOS.")

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
            if not pattern_str:
                continue

            try:
                # Get settings with defaults
                case_sensitive = pattern_config.get("case_sensitive", False)
                multiline = pattern_config.get("multiline", False)
                
                if HAS_RE2:
                    # re2 uses an Options object for flags
                    options = re2.Options()
                    options.case_sensitive = case_sensitive
                    options.multiline = multiline
                        
                    compiled = re2.compile(pattern_str, options=options)
                else:
                    flags = 0
                    if not case_sensitive:
                        flags |= re.IGNORECASE
                    if multiline:
                        flags |= re.MULTILINE
                    compiled = re.compile(pattern_str, flags)

                severity = self._parse_severity(
                    pattern_config.get("severity", "medium")
                )
                name = pattern_config.get("name", pattern_str[:30])
                self._compiled_patterns.append((compiled, severity, name, pattern_str))
                logger.debug(f"Compiled automod regex '{pattern_str}' (case_sensitive={case_sensitive}, multiline={multiline})")
            except Exception as e:
                logger.error(f"Failed to compile automod regex '{pattern_str}': {e}")

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

        for compiled, severity, name, pattern_str in self._compiled_patterns:
            # Both re and re2 support search()
            try:
                match = compiled.search(content)
                if match:
                    logger.debug(f"Regex rule '{self.rule.name}' matched pattern '{pattern_str}' in content from user {user_id}")
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
            except Exception as e:
                logger.error(f"Error matching regex pattern '{pattern_str}': {e}")

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
        return mapping.get(str(sev_str).lower(), ViolationSeverity.MEDIUM)

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
                    if HAS_RE2:
                        # re2 is naturally safe from ReDoS
                        re2.compile(pattern_str)
                    else:
                        # Fallback validation if re2 is missing (legacy protection)
                        re.compile(pattern_str)
                        if len(pattern_str) > 500:
                            issues.append(f"pattern {i} exceeds maximum length")
                        # Basic check for nested quantifiers which are dangerous in 're'
                        if re.search(r'\(.*\)[*+?]', pattern_str):
                             issues.append(f"pattern {i} contains potential ReDoS risk (nested group quantifier). Install google-re2 for better protection.")

                except Exception as e:
                    issues.append(f"pattern {i} invalid regex: {e}")

        return len(issues) == 0, issues
