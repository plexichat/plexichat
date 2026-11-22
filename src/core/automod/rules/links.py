"""
Link filtering rules.

Handles server invite links and external link whitelist/blacklist.
"""

import re
import unicodedata
from typing import Dict, Any, Optional, Set, List
from urllib.parse import unquote

import utils.logger as logger

from src.core.security.hardening import HardeningViolation, bounded_int

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


# Invisible format/control codepoints (zero-width space/joiner, bidi overrides,
# soft hyphen, BOM, NUL, ...) are visually undetectable yet change the bytes of
# a message. The keyword/duplicate-spam layers already fold them away through
# ``canonicalize_for_matching``; the link layers must do the same or a single
# U+200B spliced into a URL silently defeats the domain blocklist and the invite
# code detector. Cf = format, Cc = control — matching the shared canonicalizer
# so both moderation layers agree on what "the same text" means.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc"})

# A literal IPv4 host shape, compiled once at import. ``_extract_host`` reaches
# this check on every message (and on every token that is not a name-shaped
# host), so compiling it inline on that hot path was pure repeated work. A
# compiled pattern is immutable and its ``match`` is thread-safe; no per-user
# state is involved.
_IPV4_HOST_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

# ``validate_config`` caps allowlists at 200 entries, but stored rule configs
# are rebuilt into rule objects on every worker start WITHOUT revalidation.
# Apply the same ceiling at construction so a stale or hand-edited row cannot
# load an unbounded allowlist into every process.
MAX_ALLOWED_CODES = 200


def _fold_link_text(text: str) -> str:
    """NFKC-fold a message and delete invisible characters.

    Deleting invisibles restores URLs whose scheme/host/path was split by a
    zero-width separator (``discord\u200b.gg/x`` -> ``discord.gg/x``) and folds
    compatibility forms (fullwidth letters) to the ASCII shapes the detectors
    understand. No lowercasing: reporting keeps the original casing.
    """
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKC", text)
        if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )


def _separate_invisible(text: str) -> str:
    """Replace invisible characters with a space instead of deleting them.

    Complementary to :func:`_fold_link_text`: when an attacker *glues* a domain
    to surrounding prose with a zero-width separator (``visit\u200bevil.com``),
    deleting it would produce one unrecognisable token. Turning it into a space
    restores the word boundary so the domain is tokenised on its own.
    """
    return "".join(
        " " if unicodedata.category(ch) in _INVISIBLE_CATEGORIES else ch
        for ch in unicodedata.normalize("NFKC", text)
    )


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
    # A trailing DNS-root dot (``discord.gg./``) resolves to the same host and
    # is already tolerated by the ``[A-Za-z0-9.-]+/invite/`` branch; allow it on
    # the short form too so ``discord.gg./<code>`` cannot evade moderation.
    # The dot is optional and must be immediately followed by ``/``, so a
    # different host (``discord.gg.evil.example``) is never mistaken for it.
    _INVITE_SHAPE_RE = re.compile(
        r"(?:[A-Za-z0-9.-]+/invite/|discord\.gg\.?/)([A-Za-z0-9_-]{4,})",
        re.IGNORECASE,
    )

    # Invite codes are bearer credentials minted with ``token_urlsafe(16)``:
    # 22 characters from ``[A-Za-z0-9_-]``. The configurable ``_pattern``
    # (default ``[a-zA-Z0-9]{8}``) cannot match them, so extracting candidates
    # from that pattern alone made a real 22-char code invisible to BOTH the
    # "confirmed invite" and the "invite-shaped URL" branches: a live invite
    # link could be posted in chat without moderation noticing. Collect
    # urlsafe-shaped candidates independently of the configured pattern so any
    # current or future code length is handled. These are only *candidates*;
    # aggressive blocking still requires confirmation or an invite URL shape,
    # so ordinary words are not mass-blocked (fail closed, no false positives).
    _URLSAFE_CODE_RE = re.compile(r"[A-Za-z0-9_-]{4,64}")

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._block_all: bool = self.config.get("block_all", True)
        self._warn_only: bool = self.config.get("warn_only", False)
        # Stored configs bypass validate_config(), so bound the admin-supplied
        # fields again at construction time. An unbounded ``allowed_codes``
        # list is loaded into every worker; a huge/negative/non-integer
        # ``code_length`` is interpolated into a regex quantifier and used to
        # raise OverflowError inside rule construction (breaking message
        # moderation for the server), or to build a pattern that never matches.
        raw_allowed = self.config.get("allowed_codes", [])
        if not isinstance(raw_allowed, (list, tuple)):
            raw_allowed = []
        self._allowed_codes: Set[str] = set(
            c.lower()
            for c in raw_allowed[:MAX_ALLOWED_CODES]
            if isinstance(c, str) and c
        )
        from ..limits import MAX_CODE_LENGTH

        try:
            self._code_length: int = bounded_int(
                self.config.get("code_length", 8),
                "code_length",
                minimum=1,
                maximum=MAX_CODE_LENGTH,
                default=8,
            )
        except HardeningViolation:
            self._code_length = 8

        custom_pattern = self.config.get("pattern")
        if isinstance(custom_pattern, str) and custom_pattern:
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
        elif custom_pattern:
            # A malformed stored value (list/int/...) must fall back to the
            # canonical compiled pattern, not a freshly compiled lookalike:
            # consumers rely on identity to detect the fallback.
            logger.warning("InviteLinkRule custom pattern rejected: not a string")
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
        content = content or ""

        # Scan two complementary canonical views of the message. An attacker
        # can hide an invite behind invisible separators inside the URL (a
        # zero-width space after ``discord.gg/``) or glue it to a word; one view
        # deletes the invisibles, the other turns them into word boundaries.
        # Both are needed — deleting alone would fuse ``join<ZWSP>discord.gg``
        # into an unrecognisable token, while splitting alone would break a code
        # spliced in the middle.
        potential_codes: List[str] = []
        shaped_lower: Set[str] = set()

        def _add(candidates) -> None:
            for code in candidates:
                if code not in potential_codes:
                    potential_codes.append(code)

        for view in (_fold_link_text(content), _separate_invisible(content)):
            # The configured pattern is kept for admin-tuned detections (and
            # existing deployments), but it is no longer the ONLY source of
            # candidates: a fresh 22-char invite code would be missed by it.
            _add(self._pattern.findall(view))
            _add(self._URLSAFE_CODE_RE.findall(view))
            shaped = self._INVITE_SHAPE_RE.findall(view)
            _add(shaped)
            shaped_lower.update(code.lower() for code in shaped)

        confirmed = [
            code
            for code in potential_codes
            if code.lower() in known_invites or code in known_invites
        ]

        # Aggressive blocking is restricted to invite-SHAPED candidates (codes
        # inside an invite URL) plus confirmed codes. Generic pattern matches
        # are indicators only: blocking every 8-char word just because some
        # other invite was confirmed would mass false-positive.
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

        if "code_length" in config:
            from ..limits import MAX_CODE_LENGTH

            if config["code_length"] > MAX_CODE_LENGTH:
                issues.append(f"code_length exceeds {MAX_CODE_LENGTH}")

        if "allowed_codes" in config and isinstance(config["allowed_codes"], list):
            if len(config["allowed_codes"]) > 200:
                issues.append("allowed_codes exceeds 200 entries")

        if "pattern" in config:
            from .regex import MAX_PATTERN_LENGTH, _has_redos_risk

            pattern = config["pattern"]
            if not isinstance(pattern, str):
                issues.append("pattern must be a string")
            elif len(pattern) > MAX_PATTERN_LENGTH:
                issues.append(f"pattern exceeds maximum length of {MAX_PATTERN_LENGTH}")
            elif _has_redos_risk(pattern):
                issues.append("pattern has ReDoS risk on the fallback engine")
            else:
                try:
                    re.compile(pattern)
                except re.error as e:
                    issues.append(f"invalid regex pattern: {e}")

        return len(issues) == 0, issues


def _canonicalize_host(value: str) -> str:
    """Fold a host to the single shape both policy lists and observed hosts use.

    A URL's authority is decoded by the consumer before DNS resolution:
    percent-encoded bytes are decoded (`evil%2eexample` -> `evil.example`,
    `%65vil.example` -> `evil.example`) and internationalised names are
    converted to their ASCII/punycode form (`xn--...`, or a Unicode label
    such as an IDN homoglyph). The moderation layer must decide on that SAME
    name. Applying the canonicalization to both list entries and extracted
    hosts closes two parser differentials that otherwise fail open:

    * an admin's punycode entry (``xn--...``) never matched a Unicode host,
      and a Unicode entry never matched a punycode host; and
    * a URL with a percent-encoded host (``evil%2eexample``,
      ``%65vil.example``) was reported as "not a link at all" and bypassed
      both blacklist and whitelist modes even though the browser resolved
      the decoded name.

    A trailing DNS-root dot is stripped for DNS equivalence. Decoding is
    repeated because an encoded `%25` chain can expose another percent escape,
    and bounded to avoid pathological nesting.
    """
    value = value.strip()
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    try:
        value = value.encode("idna").decode("ascii")
    except Exception:
        pass
    # Strip ALL trailing root markers, not just one: ``evil.example..`` is
    # DNS-equivalent to ``evil.example`` and must match the same list entry.
    return value.lower().rstrip(".")


def _normalize_listed_domain(entry: str) -> str:
    """Normalize a whitelist/blacklist entry to the observed-host shape.

    Entries go through the same canonicalization as extracted hosts (percent
    decoding, IDNA/punycode, lowercasing, trailing DNS-root dot removal), so
    an admin-supplied ``"Evil.COM. "`` or ``"xn--..."`` matches exactly what
    ``ExternalLinkRule._extract_host`` observes.
    """
    return _canonicalize_host(entry)


class ExternalLinkRule(BaseRule):
    """Rule that filters external links with whitelist/blacklist.

    Link detection is SCHEME-INDEPENDENT: any scheme (``https://``,
    ``ftp://``, ``steam://`` ...), protocol-relative URLs (``//host``) and
    bare domains (``example.com/path``) are all extracted, so a filter
    bypass via an unusual or missing scheme is not possible. A bare host with
    a port (``example.com:8080/path``) is read as that host too -- the port is
    dropped, not mistaken for a scheme separator. A trailing DNS-root dot
    (``example.com./path``) is stripped as well, since it resolves to the
    same host. A literal backslash in the authority is treated as a path
    separator (as browsers do): ``https://evil.example\\@trusted.example/`` is
    judged on ``evil.example``, not on the ``@``-suffix a stdlib parser reports.
    Note that any
    domain-shaped token (two-plus dot-separated labels with an alphabetic
    TLD) is treated as a link; whitelist mode will therefore also match
    bare filenames such as ``file.txt``.

    Whitelist/blacklist entries match the exact domain AND every subdomain
    of it: whitelisting ``example.com`` also allows ``api.example.com``
    (and likewise for the blacklist). Entries are normalized to the same
    shape as observed hosts (lowercased, surrounding whitespace and a
    trailing DNS-root dot stripped), so an FQDN-form entry such as
    ``"Example.COM. "`` matches exactly what ``"example.com"`` matches.
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
            _normalize_listed_domain(d)
            for d in self._bounded_domains(self.config.get("whitelist", []))
        )
        self._blacklist: Set[str] = set(
            _normalize_listed_domain(d)
            for d in self._bounded_domains(self.config.get("blacklist", []))
        )
        self._block_all: bool = self.config.get("block_all", False)

    @staticmethod
    def _bounded_domains(value: Any) -> List[str]:
        """Trim a stored domain list to a finite, length-bounded set."""
        from ..limits import MAX_DOMAIN_LENGTH, MAX_LISTED_DOMAINS

        if not isinstance(value, (list, tuple)):
            return []
        return [
            d[:MAX_DOMAIN_LENGTH] for d in value if isinstance(d, str) and d.strip()
        ][:MAX_LISTED_DOMAINS]

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
        # Two complementary canonical views close the invisible-character
        # evasion class: deleting invisibles restores a URL split inside its
        # scheme/host, while replacing them with spaces restores a domain the
        # sender glued to adjacent prose with a zero-width separator. Without
        # this, a single U+200B inside ``evil.com`` made _extract_host return
        # None and both whitelist and blacklist modes failed open.
        content = content or ""
        for view in (_fold_link_text(content), _separate_invisible(content)):
            for token in self._TOKEN_SPLIT.split(view):
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

            # WHATWG URL parsers (browsers, http(s) clients) treat a literal
            # backslash in a special URL as an authority/path separator, so
            # ``https://evil.example\@trusted.example/`` opens ``evil.example``
            # (the host on the LEFT of the backslash) and demotes
            # ``@trusted.example`` to path data. A plain ``urlsplit``-style
            # parser instead keeps the backslash in the authority and reports
            # the host to the RIGHT of ``@`` -- exactly what this extractor
            # used to do. That parser differential let a denylisted host pass
            # in blacklist mode and a non-allowlisted host pass in whitelist
            # mode while the browser opened the blocked/unlisted host.
            # Canonicalize backslashes to slashes BEFORE authority parsing so
            # the moderation decision and the consumer agree on the host.
            # Percent-encoded ``%5C`` and backslashes in the path are data,
            # not authority separators, and are deliberately left untouched.
            candidate = candidate.replace("\\", "/")
            # Browsers collapse any run of '/' or '\' after a special scheme's
            # colon into the authority delimiter: ``https:\host`` and
            # ``https:///host`` both open ``host``. After the replacement above
            # that is a run of one or more slashes, which a urlsplit-style
            # parser would treat as an empty host/path. Normalize it to ``//``
            # so the authority is parsed instead of silently dropped. Runs with
            # no slash at all (``host:8080``) are left alone -- those are the
            # bare host:port shape handled below.
            candidate = re.sub(
                r"^([a-zA-Z][a-zA-Z0-9+.\-]*:)/+", r"\1//", candidate, count=1
            )

            scheme_match = self._SCHEME_PREFIX.match(candidate)
            has_scheme = scheme_match is not None
            if has_scheme and scheme_match is not None:
                remainder = candidate[scheme_match.end() :]
                # A dotted prefix followed by a numeric remainder is a bare
                # host:port ("evil.com:8080/x"), not a scheme. Stripping it
                # would discard the host and report the token as "not a link
                # at all", making a port a one-token bypass of both modes.
                # Scheme names may contain dots (RFC 3986) but never start
                # their remainder with a port digit, so the shapes stay
                # distinguishable.
                scheme_text = candidate[: scheme_match.end()]
                is_authority = scheme_text.endswith("//")
                prefix = candidate[: scheme_match.end() - 1]
                # Only a *bare* "host:port" (no ``//`` authority marker)
                # gets reclassified; an explicit ``scheme://host`` is always
                # a scheme even when the host's first label starts with a
                # digit.
                if (not is_authority) and "." in prefix and remainder[:1].isdigit():
                    has_scheme = False
                else:
                    # Any explicit scheme counts as a link attempt (ftp:,
                    # https:, custom app schemes ...); strip it before host
                    # parsing.
                    candidate = remainder
            elif candidate.startswith("//"):
                # Protocol-relative URL.
                candidate = candidate[2:]
                has_scheme = True

            authority = candidate.split("/", 1)[0]
            authority = authority.split("?", 1)[0].split("#", 1)[0]

            # Canonicalize the authority the way the consumer does BEFORE
            # deciding it is "not a link". Percent-encoded bytes in a host
            # (``evil%2eexample`` -> ``evil.example``, ``%65vil.example``)
            # and internationalised/punycode labels are decoded into the name
            # the browser actually resolves; a raw ``%2F``/``%5C`` decodes to
            # a separator that must not be smuggled into the host. Split the
            # authority first (so userinfo/port are dropped), then decode the
            # host fragment; if decoding introduces a separator the token is
            # malformed as an authority and is rejected (fail closed).
            host = authority.rsplit("@", 1)[-1].split(":", 1)[0]
            host = _canonicalize_host(host)
            if any(
                sep in host
                for sep in ("/", "\\", "?", "#", "@", ":", "[", "]", " ", "\t", "\r", "\n")
            ):
                return None
            if not host:
                return None

            if host.startswith("www."):
                host = host[4:]

            if not self._BARE_HOST.match(host):
                # Not a name-shaped host; only accept it when an explicit
                # scheme marked the token as a URL (covers raw IPs etc.).
                if not has_scheme:
                    return None
                if not _IPV4_HOST_RE.match(host):
                    return None

            # Require an alphabetic (or punycode ``xn--``) TLD of length >= 2
            # so single-letter dotted fragments ("e.g") are not treated as
            # hosts. A punycode TLD such as ``.xn--p1ai`` is a real ccTLD and
            # must be accepted, or a denylisted/foreign IDN host would slip
            # past both modes. Raw IPv4 hosts are exempt (validated above).
            tld = host.rsplit(".", 1)[-1]
            if len(tld) < 2 or not (tld.isalpha() or tld.startswith("xn--")):
                if not _IPV4_HOST_RE.match(host):
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
                else:
                    from ..limits import MAX_DOMAIN_LENGTH, MAX_LISTED_DOMAINS

                    if len(value) > MAX_LISTED_DOMAINS:
                        issues.append(f"{field} exceeds {MAX_LISTED_DOMAINS} entries")
                    elif any(len(v) > MAX_DOMAIN_LENGTH for v in value):
                        issues.append(
                            f"{field} entries exceed {MAX_DOMAIN_LENGTH} characters"
                        )

        if "block_all" in config and not isinstance(config["block_all"], bool):
            issues.append("block_all must be a boolean")

        return len(issues) == 0, issues
