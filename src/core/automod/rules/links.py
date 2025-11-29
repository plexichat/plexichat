"""
Link filter rule - Filters invite links and external URLs.
"""

import re
from typing import Dict, Any, List
from urllib.parse import urlparse

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity


class LinkFilterRule(BaseRule):
    """Rule that filters invite links and external URLs."""
    
    DISCORD_INVITE_PATTERN = re.compile(
        r'(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discordapp\.com/invite)/([a-zA-Z0-9-]+)',
        re.IGNORECASE
    )
    
    URL_PATTERN = re.compile(
        r'https?://[^\s<>"{}|\\^`\[\]]+',
        re.IGNORECASE
    )
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._block_invites = self.config.get("block_invites", True)
        self._block_external_links = self.config.get("block_external_links", False)
        self._whitelist_domains = self._normalize_domains(self.config.get("whitelist_domains", []))
        self._blacklist_domains = self._normalize_domains(self.config.get("blacklist_domains", []))
        self._allow_own_server_invites = self.config.get("allow_own_server_invites", True)
    
    def _normalize_domains(self, domains: List[str]) -> List[str]:
        """Normalize domain list to lowercase."""
        return [d.lower().strip() for d in domains if d.strip()]
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check for blocked links."""
        violations = []
        matched_links = []
        
        if self._block_invites:
            invites = self.DISCORD_INVITE_PATTERN.findall(content)
            if invites:
                for invite_code in invites:
                    if self._allow_own_server_invites:
                        server_invites = context.get("server_invite_codes", [])
                        if invite_code in server_invites:
                            continue
                    violations.append(f"invite:{invite_code}")
                    matched_links.append(f"discord invite: {invite_code}")
        
        urls = self.URL_PATTERN.findall(content)
        
        for url in urls:
            domain = self._extract_domain(url)
            if not domain:
                continue
            
            if self._blacklist_domains and domain in self._blacklist_domains:
                violations.append(f"blacklisted:{domain}")
                matched_links.append(url)
                continue
            
            if self._block_external_links:
                if self._whitelist_domains:
                    if domain not in self._whitelist_domains:
                        violations.append(f"external:{domain}")
                        matched_links.append(url)
                else:
                    violations.append(f"external:{domain}")
                    matched_links.append(url)
        
        if not violations:
            return RuleMatch(matched=False)
        
        severity = self._calculate_link_severity(violations)
        
        return RuleMatch(
            matched=True,
            severity=severity,
            matched_content=", ".join(matched_links[:3]),
            trigger_details={
                "violations": violations,
                "link_count": len(matched_links),
                "matched_links": matched_links[:10],
            }
        )
    
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return ""
    
    def _calculate_link_severity(self, violations: List[str]) -> ViolationSeverity:
        """Calculate severity based on violations."""
        invite_count = sum(1 for v in violations if v.startswith("invite:"))
        blacklist_count = sum(1 for v in violations if v.startswith("blacklisted:"))
        
        if blacklist_count > 0:
            return ViolationSeverity.HIGH
        if invite_count >= 3:
            return ViolationSeverity.HIGH
        if invite_count > 0:
            return ViolationSeverity.MEDIUM
        return ViolationSeverity.LOW
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate link filter rule configuration."""
        issues = []
        
        for key in ["block_invites", "block_external_links", "allow_own_server_invites"]:
            if key in config and not isinstance(config[key], bool):
                issues.append(f"{key} must be a boolean")
        
        for key in ["whitelist_domains", "blacklist_domains"]:
            value = config.get(key)
            if value is not None:
                if not isinstance(value, list):
                    issues.append(f"{key} must be a list")
                elif len(value) > 500:
                    issues.append(f"{key} cannot exceed 500 items")
                else:
                    for i, domain in enumerate(value):
                        if not isinstance(domain, str):
                            issues.append(f"{key}[{i}] must be a string")
                        elif len(domain) > 253:
                            issues.append(f"{key}[{i}] exceeds maximum domain length")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "block_invites": True,
            "block_external_links": False,
            "whitelist_domains": [],
            "blacklist_domains": [],
            "allow_own_server_invites": True,
        }
