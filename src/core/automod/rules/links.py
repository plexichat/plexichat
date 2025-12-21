"""
Link filtering rules.

Handles server invite links and external link whitelist/blacklist.
"""

import re
from typing import Dict, Any, Optional, Set
from urllib.parse import urlparse

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class InviteLinkRule(BaseRule):
    """Rule that detects and blocks server invite codes."""

    rule_type = RuleType.INVITE_LINKS

    DEFAULT_INVITE_PATTERN = re.compile(r"\b([a-zA-Z0-9]{8})\b")

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._block_all: bool = self.config.get("block_all", True)
        self._allowed_codes: Set[str] = set(c.lower() for c in self.config.get("allowed_codes", []))
        self._code_length: int = self.config.get("code_length", 8)

        custom_pattern = self.config.get("pattern")
        if custom_pattern:
            try:
                self._pattern = re.compile(custom_pattern)
            except re.error:
                self._pattern = self.DEFAULT_INVITE_PATTERN
        else:
            self._pattern = re.compile(rf"\b([a-zA-Z0-9]{{{self._code_length}}})\b")

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """Check for invite codes."""
        context = context or {}
        known_invites = set(context.get("known_invite_codes", []))

        potential_codes = self._pattern.findall(content)

        found_invites = []
        for code in potential_codes:
            if code.lower() in known_invites or code in known_invites:
                found_invites.append(code)

        if not found_invites:
            return self._no_match()

        if not self._block_all:
            return self._no_match()

        blocked_invites = []
        for invite_code in found_invites:
            if invite_code.lower() in self._allowed_codes:
                continue
            blocked_invites.append(invite_code)

        if not blocked_invites:
            return self._no_match()

        return self._create_match(
            matched=True,
            matched_content=", ".join(blocked_invites),
            details={
                "invite_codes": blocked_invites,
                "count": len(blocked_invites)
            },
            severity=ViolationSeverity.MEDIUM
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate invite link rule configuration."""
        issues = []

        if "block_all" in config and not isinstance(config["block_all"], bool):
            issues.append("block_all must be a boolean")

        if "allowed_codes" in config:
            value = config["allowed_codes"]
            if not isinstance(value, list):
                issues.append("allowed_codes must be a list")
            elif not all(isinstance(v, str) for v in value):
                issues.append("allowed_codes must contain only strings")

        if "code_length" in config:
            if not isinstance(config["code_length"], int) or config["code_length"] < 1:
                issues.append("code_length must be a positive integer")

        if "pattern" in config:
            try:
                re.compile(config["pattern"])
            except re.error as e:
                issues.append(f"invalid regex pattern: {e}")

        return len(issues) == 0, issues


class ExternalLinkRule(BaseRule):
    """Rule that filters external links with whitelist/blacklist."""

    rule_type = RuleType.EXTERNAL_LINKS

    URL_PATTERN = re.compile(
        r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*",
        re.IGNORECASE
    )

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._mode: str = self.config.get("mode", "blacklist")
        self._whitelist: Set[str] = set(d.lower() for d in self.config.get("whitelist", []))
        self._blacklist: Set[str] = set(d.lower() for d in self.config.get("blacklist", []))
        self._block_all: bool = self.config.get("block_all", False)

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """Check for external links."""
        urls = self.URL_PATTERN.findall(content)

        if not urls:
            return self._no_match()

        blocked_urls = []
        blocked_domains = []

        for url in urls:
            domain = self._extract_domain(url)
            if not domain:
                continue

            if self._block_all:
                blocked_urls.append(url)
                blocked_domains.append(domain)
                continue

            if self._mode == "whitelist":
                if not self._domain_in_list(domain, self._whitelist):
                    blocked_urls.append(url)
                    blocked_domains.append(domain)
            else:
                if self._domain_in_list(domain, self._blacklist):
                    blocked_urls.append(url)
                    blocked_domains.append(domain)

        if not blocked_urls:
            return self._no_match()

        return self._create_match(
            matched=True,
            matched_content=", ".join(blocked_domains[:5]),
            details={
                "blocked_urls": blocked_urls,
                "blocked_domains": blocked_domains,
                "count": len(blocked_urls),
                "mode": self._mode
            },
            severity=ViolationSeverity.MEDIUM
        )

    def _extract_domain(self, url: str) -> Optional[str]:
        """Extract domain from URL."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return None

    def _domain_in_list(self, domain: str, domain_list: Set[str]) -> bool:
        """Check if domain or parent domain is in list."""
        if domain in domain_list:
            return True

        parts = domain.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in domain_list:
                return True

        return False

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate external link rule configuration."""
        issues = []

        mode = config.get("mode", "blacklist")
        if mode not in ["whitelist", "blacklist"]:
            issues.append("mode must be 'whitelist' or 'blacklist'")

        for field in ["whitelist", "blacklist"]:
            value = config.get(field)
            if value is not None:
                if not isinstance(value, list):
                    issues.append(f"{field} must be a list")
                elif not all(isinstance(v, str) for v in value):
                    issues.append(f"{field} must contain only strings")

        if "block_all" in config and not isinstance(config["block_all"], bool):
            issues.append("block_all must be a boolean")

        return len(issues) == 0, issues
