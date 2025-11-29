"""
Keyword filter rule - Matches messages containing blocked keywords.
"""

from typing import Dict, Any, List

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity


class KeywordRule(BaseRule):
    """Rule that matches messages containing blocked keywords."""
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._keywords = self._normalize_keywords(self.config.get("keywords", []))
        self._match_whole_word = self.config.get("match_whole_word", True)
        self._case_sensitive = self.config.get("case_sensitive", False)
    
    def _normalize_keywords(self, keywords: List[str]) -> List[str]:
        """Normalize keywords for matching."""
        if self._case_sensitive:
            return [k.strip() for k in keywords if k.strip()]
        return [k.strip().lower() for k in keywords if k.strip()]
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check content for blocked keywords."""
        if not self._keywords:
            return RuleMatch(matched=False)
        
        check_content = content if self._case_sensitive else content.lower()
        matched_keywords = []
        
        for keyword in self._keywords:
            if self._match_whole_word:
                if self._word_match(check_content, keyword):
                    matched_keywords.append(keyword)
            else:
                if keyword in check_content:
                    matched_keywords.append(keyword)
        
        if not matched_keywords:
            return RuleMatch(matched=False)
        
        severity = self._calculate_severity(len(matched_keywords))
        
        return RuleMatch(
            matched=True,
            severity=severity,
            matched_content=", ".join(matched_keywords[:5]),
            trigger_details={
                "matched_keywords": matched_keywords,
                "match_count": len(matched_keywords),
            }
        )
    
    def _word_match(self, content: str, keyword: str) -> bool:
        """Check if keyword exists as a whole word in content."""
        import re
        pattern = r'\b' + re.escape(keyword) + r'\b'
        return bool(re.search(pattern, content))
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate keyword rule configuration."""
        issues = []
        
        keywords = config.get("keywords")
        if keywords is None:
            issues.append("keywords is required")
        elif not isinstance(keywords, list):
            issues.append("keywords must be a list")
        elif len(keywords) == 0:
            issues.append("keywords list cannot be empty")
        elif len(keywords) > 1000:
            issues.append("keywords list cannot exceed 1000 items")
        else:
            for i, kw in enumerate(keywords):
                if not isinstance(kw, str):
                    issues.append(f"keyword at index {i} must be a string")
                elif len(kw.strip()) == 0:
                    issues.append(f"keyword at index {i} cannot be empty")
                elif len(kw) > 100:
                    issues.append(f"keyword at index {i} exceeds 100 characters")
        
        if "match_whole_word" in config and not isinstance(config["match_whole_word"], bool):
            issues.append("match_whole_word must be a boolean")
        
        if "case_sensitive" in config and not isinstance(config["case_sensitive"], bool):
            issues.append("case_sensitive must be a boolean")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "keywords": [],
            "match_whole_word": True,
            "case_sensitive": False,
        }
