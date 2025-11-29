"""
Keyword filter rule.

Checks messages for blocked keywords with optional word boundary matching.
"""

from typing import Dict, Any, Optional, List

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class KeywordRule(BaseRule):
    """Rule that checks for blocked keywords."""
    
    rule_type = RuleType.KEYWORD
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._keywords: List[str] = self.config.get("keywords", [])
        self._case_sensitive: bool = self.config.get("case_sensitive", False)
        self._whole_word: bool = self.config.get("whole_word", True)
        self._severity_map: Dict[str, str] = self.config.get("severity_map", {})
        
        if not self._case_sensitive:
            self._keywords = [k.lower() for k in self._keywords]
            self._severity_map = {k.lower(): v for k, v in self._severity_map.items()}
    
    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """Check content for blocked keywords."""
        if not self._keywords:
            return self._no_match()
        
        check_content = content if self._case_sensitive else content.lower()
        
        matched_keywords = []
        highest_severity = ViolationSeverity.LOW
        
        for keyword in self._keywords:
            if self._whole_word:
                if self._contains_whole_word(check_content, keyword):
                    matched_keywords.append(keyword)
            else:
                if keyword in check_content:
                    matched_keywords.append(keyword)
        
        if not matched_keywords:
            return self._no_match()
        
        for kw in matched_keywords:
            sev_str = self._severity_map.get(kw, "medium")
            sev = self._parse_severity(sev_str)
            if self._severity_rank(sev) > self._severity_rank(highest_severity):
                highest_severity = sev
        
        return self._create_match(
            matched=True,
            matched_content=", ".join(matched_keywords),
            details={
                "keywords": matched_keywords,
                "count": len(matched_keywords)
            },
            severity=highest_severity
        )
    
    def _contains_whole_word(self, text: str, word: str) -> bool:
        """Check if text contains word as a whole word."""
        word_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
        
        start = 0
        while True:
            pos = text.find(word, start)
            if pos == -1:
                return False
            
            before_ok = pos == 0 or text[pos - 1] not in word_chars
            after_pos = pos + len(word)
            after_ok = after_pos >= len(text) or text[after_pos] not in word_chars
            
            if before_ok and after_ok:
                return True
            
            start = pos + 1
        
        return False
    
    def _parse_severity(self, sev_str: str) -> ViolationSeverity:
        """Parse severity string to enum."""
        mapping = {
            "low": ViolationSeverity.LOW,
            "medium": ViolationSeverity.MEDIUM,
            "high": ViolationSeverity.HIGH,
            "critical": ViolationSeverity.CRITICAL
        }
        return mapping.get(sev_str.lower(), ViolationSeverity.MEDIUM)
    
    def _severity_rank(self, sev: ViolationSeverity) -> int:
        """Get numeric rank for severity comparison."""
        ranks = {
            ViolationSeverity.LOW: 1,
            ViolationSeverity.MEDIUM: 2,
            ViolationSeverity.HIGH: 3,
            ViolationSeverity.CRITICAL: 4
        }
        return ranks.get(sev, 2)
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate keyword rule configuration."""
        issues = []
        
        keywords = config.get("keywords")
        if not keywords:
            issues.append("keywords list is required")
        elif not isinstance(keywords, list):
            issues.append("keywords must be a list")
        elif not all(isinstance(k, str) for k in keywords):
            issues.append("all keywords must be strings")
        
        if "case_sensitive" in config and not isinstance(config["case_sensitive"], bool):
            issues.append("case_sensitive must be a boolean")
        
        if "whole_word" in config and not isinstance(config["whole_word"], bool):
            issues.append("whole_word must be a boolean")
        
        return len(issues) == 0, issues
