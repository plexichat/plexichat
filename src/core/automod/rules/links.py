"""
Link filtering rules.

Handles server invite links and external link whitelist/blacklist.
"""

import re
from typing import Dict, Any, Optional, Set

import utils.logger as logger

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class InviteLinkRule(BaseRule):
    """Rule that detects and blocks server invite codes.

    Semantics:

    - ``block_all=True`` (default): enforcement covers (a) codes CONFIRMED
      against ``context["known_invite_codes"]`` and (b) invite-SHAPED
      candidates - codes appearing inside an invite URL (``discord.gg/<code>``,
      ``<host>/invite/<code>``, ...). Generic pattern matches (e.g. any
      8-character word) are treated as INDICATORS ONLY and never blocked on
      their own, so ordinary words cannot be mass-blocked just because some
      other confirmed invite exists.
    - ``block_all=False`` (allowlist mode): only invites CONFIRMED against
      ``context["known_invite_codes"]`` are enforced on; any confirmed code
      not in ``allowed_codes`` is blocked - i.e. non-allowlisted invites are
      still blocked (this no longer disables enforcement entirely).
    - ``warn_only`` (default False): detections are logged as warnings but
      never enforced - pure warn-only mode via this separate flag.
    - ``allowed_codes``: the allowlist; entries here are never blocked in
      either mode.
    """

    rule_type = RuleType.INVITE_LINKS

    DEFAULT_INVITE_PATTERN = re.compile(r"\b([a-zA-Z0-9]{8})\b")

    # Invite-shaped candidates: a code embedded in an invite URL. Any host
    # with an ``/invite/<code>`` path plus the bare discord.gg short form.
    _INVITE_SHAPE_RE = re.compile(
        r"(?:[A-Za-z0-9.-]+/invite/|discord\.gg/)([A-Za-z0-9_-]{4,})",
        re.IGNORECASE,
    )

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._block_all: bool = self.config.get("block_all", True)
        self._warn_only: bool = self.config.get("warn_only", False)
        self._allowed_codes: Set[str] = set(
            c.lower() for c in self.config.get("allowed_codes", [])
        )
        self._code_length: int = self.config.get("code_length", 8)

        custom_pattern = self.config.get("pattern")
        if custom_pattern:
            try:
                # Admin-supplied input at the same trust level as regex-rule
                # patterns: apply the same ReDoS static vetting before use.
                from .regex import MAX_PATTERN_LENGTH, _has_redos_risk

                if len(custom_pattern) > MAX_PATTERN_LENGTH:
                    logger.warning(
                        "InviteLinkRule custom pattern rejected: exceeds maximum length"
                    )
                    self._pattern = self.DEFAULT_INVITE_PATTERN
                elif _has_redos_risk(custom_pattern):
                    logger.warning(
                        "InviteLinkRule custom pattern rejected: ReDoS risk on fallback engine"
                    )
                    self._pattern = self.DEFAULT_INVITE_PATTERN
                else:
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
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for invite codes."""
        context = context or {}
        known_invites = set(context.get("known_invite_codes", []))

        potential_codes = self._pattern.findall(content)

        confirmed = [
            code
            for code in potential_codes
            if code.lower() in known_invites or code in known_invites
        ]

        # Aggressive blocking is restricted to invite-SHAPED candidates (codes
        # inside an invite URL) plus confirmed codes. Generic pattern matches
        # are indicators only: blocking every 8-char word just because some
        # other invite was confirmed would mass false-positive.
        shaped_lower = {
            code.lower() for code in self._INVITE_SHAPE_RE.findall(content or "")
        }
        if self._block_all:
            detected = [
                code
                for code in potential_codes
                if code in confirmed or code.lower() in shaped_lower
            ]
        else:
            detected = confirmed

        if not detected:
            return self._no_match()

        blocked_invites = []
        for invite_code in detected:
            if invite_code.lower() in self._allowed_codes:
                continue
            blocked_invites.append(invite_code)

        if not blocked_invites:
            return self._no_match()

        if self._warn_only:
            logger.warning(
                f"InviteLinkRule (warn-only) detected invite codes {blocked_invites} "
                f"from user {user_id} in channel {channel_id}"
            )
            return self._no_match()

        return self._create_match(
            matched=True,
            matched_content=", ".join(blocked_invites),
            details={"invite_codes": blocked_invites, "count": len(blocked_invites)},
            severity=ViolationSeverity.MEDIUM,
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate invite link rule configuration."""
        issues = []

        if "block_all" in config and not isinstance(config["block_all"], bool):
            issues.append("block_all must be a boolean")

        if "warn_only" in config and not isinstance(config["warn_only"], bool):
            issues.append("warn_only must be a boolean")

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
    """Rule that filters external links with whitelist/blacklist.

    Link detection is SCHEME-INDEPENDENT: any scheme (``https://``,
    ``ftp://``, ``steam://`` ...), protocol-relative URLs (``//host``) and
    bare domains (``example.com/path``) are all extracted, so a filter
    bypass via an unusual or missing scheme is not possible. Note that any
    domain-shaped token (two-plus dot-separated labels with an alphabetic
    TLD) is treated as a link; whitelist mode will therefore also match
    bare filenames such as ``file.txt``.

    Whitelist/blacklist entries match the exact domain AND every subdomain
    of it: whitelisting ``example.com`` also allows ``api.example.com``
    (and likewise for the blacklist).
    """

    rule_type = RuleType.EXTERNAL_LINKS

    # Candidate tokens that may carry a host: split content on whitespace,
    # then decide per token in _extract_host.
    _TOKEN_SPLIT = re.compile(r"\s+")
    _SCHEME_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:(?://)?")
    _BARE_HOST = re.compile(
        r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?)+$"
    )

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._mode: str = self.config.get("mode", "blacklist")
        self._whitelist: Set[str] = set(
            d.lower() for d in self.config.get("whitelist", [])
        )
        self._blacklist: Set[str] = set(
            d.lower() for d in self.config.get("blacklist", [])
        )
        self._block_all: bool = self.config.get("block_all", False)

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for external links."""
        urls = []
        domains = []
        seen_hosts = set()
        for token in self._TOKEN_SPLIT.split(content or ""):
            host = self._extract_host(token)
            if not host or host in seen_hosts:
                continue
            seen_hosts.add(host)
            urls.append(token.strip("\"'()[]{}<>,;:!?."))
            domains.append(host)

        if not urls:
            return self._no_match()

        blocked_urls = []
        blocked_domains = []

        for url, domain in zip(urls, domains):
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
                "mode": self._mode,
            },
            severity=ViolationSeverity.MEDIUM,
        )

    def _extract_host(self, token: str) -> Optional[str]:
        """Extract the host from a whitespace-delimited token.

        Handles any scheme (``ftp://host``), protocol-relative (``//host``),
        and bare domains (``host/path``). Returns None when the token does
        not look like a link at all.
        """
        try:
            candidate = token.strip("\"'()[]{}<>,;:!?.")
            if not candidate:
                return None

            scheme_match = self._SCHEME_PREFIX.match(candidate)
            has_scheme = scheme_match is not None
            if has_scheme and scheme_match is not None:
                # Any explicit scheme counts as a link attempt (ftp:, https:,
                # custom app schemes ...); strip it before host parsing.
                candidate = candidate[scheme_match.end() :]
            elif candidate.startswith("//"):
                # Protocol-relative URL.
                candidate = candidate[2:]
                has_scheme = True

            host = candidate.split("/", 1)[0]
            host = host.split("?", 1)[0].split("#", 1)[0]
            # Drop userinfo and port.
            host = host.rsplit("@", 1)[-1].split(":", 1)[0]
            host = host.lower()
            if not host:
                return None

            if host.startswith("www."):
                host = host[4:]

            if not self._BARE_HOST.match(host):
                # Not a name-shaped host; only accept it when an explicit
                # scheme marked the token as a URL (covers raw IPs etc.).
                if not has_scheme:
                    return None
                ipv4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
                if not ipv4.match(host):
                    return None

            # Require an alphabetic TLD of length >= 2 so single-letter
            # dotted fragments ("e.g") are not treated as hosts; raw IPv4
            # hosts are exempt (already validated above).
            tld = host.rsplit(".", 1)[-1]
            if len(tld) < 2 or not tld.isalpha():
                if not re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$").match(host):
                    return None

            return host
        except Exception:
            return None

    def _domain_in_list(self, domain: str, domain_list: Set[str]) -> bool:
        """Check if domain or parent domain is in list.

        DOCUMENTED BEHAVIOR: every subdomain of a listed parent matches, so
        ``example.com`` in the list also matches ``api.example.com`` and
        ``deep.sub.example.com``.
        """
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
