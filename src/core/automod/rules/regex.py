"""
Regex pattern rule - Matches messages against regex patterns.
"""

import re
from typing import Dict, Any, List

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity
from ..exceptions import InvalidPatternError


class RegexRule(BaseRule):
    """Rule that matches messages against regex patterns."""
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._patterns = self._compile_patterns(self.config.get("patterns", []))
        self._flags = self._get_flags()
    
    def _get_flags(self) -> int:
        """Get regex flags from config."""
        flags = 0
        if not self.config.get("case_sensitive", False):
            flags |= re.IGNORECASE
        if self.config.get("multiline", False):
            flags |= re.MULTILINE
        if self.config.get("dotall", False):
            flags |= re.DOTALL
        return flags
    
    def _compile_patterns(self, patterns: List[str]) -> List[re.Pattern]:
        """Compile regex patterns."""
        compiled = []
        flags = self._get_flags()
        
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, flags))
            except re.error:
                continue
        
        return compiled
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check content against regex patterns."""
        if not self._patterns:
            return RuleMatch(matched=False)
        
        matches = []
        matched_patterns = []
        
        for pattern in self._patterns:
            found = pattern.findall(content)
            if found:
                matches.extend(found)
                matched_patterns.append(pattern.pattern)
        
        if not matches:
            return RuleMatch(matched=False)
        
        severity = self._calculate_severity(len(matches))
        
        matched_str = []
        for m in matches[:5]:
            if isinstance(m, tuple):
                matched_str.append(m[0] if m else "")
            else:
                matched_str.append(str(m))
        
        return RuleMatch(
            matched=True,
            severity=severity,
            matched_content=", ".join(matched_str),
            trigger_details={
                "matched_patterns": matched_patterns,
                "match_count": len(matches),
                "matches": matches[:10],
            }
        )
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate regex rule configuration."""
        issues = []
        
        patterns = config.get("patterns")
        if patterns is None:
            issues.append("patterns is required")
        elif not isinstance(patterns, list):
            issues.append("patterns must be a list")
        elif len(patterns) == 0:
            issues.append("patterns list cannot be empty")
        elif len(patterns) > 100:
            issues.append("patterns list cannot exceed 100 items")
        else:
            for i, pattern in enumerate(patterns):
                if not isinstance(pattern, str):
                    issues.append(f"pattern at index {i} must be a string")
                elif len(pattern) == 0:
                    issues.append(f"pattern at index {i} cannot be empty")
                elif len(pattern) > 500:
                    issues.append(f"pattern at index {i} exceeds 500 characters")
                else:
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        issues.append(f"pattern at index {i} is invalid: {str(e)}")
        
        for key in ["case_sensitive", "multiline", "dotall"]:
            if key in config and not isinstance(config[key], bool):
                issues.append(f"{key} must be a boolean")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "patterns": [],
            "case_sensitive": False,
            "multiline": False,
            "dotall": False,
        }
