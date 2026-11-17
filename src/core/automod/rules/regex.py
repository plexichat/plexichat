"""
Regex pattern rule.

Checks messages against configurable regex patterns.
Uses google-re2 for safe, ReDoS-resistant pattern matching.
"""

import importlib
import re
import utils.logger as logger
from typing import Dict, Any, Optional, List

from .base import BaseRule
from ..limits import (
    MAX_PATTERN_NAME_LENGTH,
    MAX_PATTERNS_PER_RULE,
    MAX_TOTAL_PATTERN_CHARS,
)
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity, coerce_severity

# Try to use google-re2 for ReDoS protection, fallback to standard re
re2_module = None
try:
    re2_module = importlib.import_module("re2")
    HAS_RE2 = True
except Exception:
    HAS_RE2 = False
    logger.warning(
        "google-re2 not installed, falling back to standard 're' module. BEWARE OF REDOS."
    )

# Hard cap on admin-supplied pattern length, enforced on EVERY engine path
# (RE2 and fallback alike).
MAX_PATTERN_LENGTH = 500

# Constructs google-re2 cannot compile; using such a pattern means falling
# back to the backtracking engine.
_FALLBACK_ONLY_MARKERS = ("(?=", "(?!", "(?<", "(?'", "(?P>", "(?(")
_BACKREFERENCE_RE = re.compile(r"\\[1-9]")


def _pattern_requires_fallback(pattern_str: str) -> bool:
    """True when the pattern needs the stdlib backtracking engine.

    RE2 rejects lookaheads/lookbehinds, backreferences and conditionals;
    any of those force a ``re.compile`` fallback.
    """
    if any(marker in pattern_str for marker in _FALLBACK_ONLY_MARKERS):
        return True
    return bool(_BACKREFERENCE_RE.search(pattern_str))


def _match_quantifier(pattern_str: str, i: int) -> Optional[int]:
    """Return length of the quantifier at ``i`` ('*', '+', '?', '{n,m}')."""
    if i >= len(pattern_str):
        return None
    ch = pattern_str[i]
    if ch in "*+?":
        return 1
    if ch == "{":
        match = re.match(r"\{\d+(,\d*)?\}", pattern_str[i:])
        if match:
            return match.end()
    return None


_UNBOUNDED = float("inf")


def _quantifier_bounds(pattern_str: str, i: int) -> Optional[tuple]:
    """Return ``(min, max)`` for the quantifier at ``i`` (``inf`` = unbounded).

    ``min != max`` marks a VARIABLE repetition count. That is what lets nested
    quantifiers explore exponentially many ways to split the same input — e.g.
    ``(a{1,2}){50}`` has as many splits as there are ways to write ``n`` as a
    sum of 1s and 2s. A fixed count (``{3}``) cannot create that ambiguity on
    its own.
    """
    if i >= len(pattern_str):
        return None
    ch = pattern_str[i]
    if ch == "*":
        return (0, _UNBOUNDED)
    if ch == "+":
        return (1, _UNBOUNDED)
    if ch == "?":
        return (0, 1)
    if ch == "{":
        match = re.match(r"\{(\d*)(,)?(\d*)\}", pattern_str[i:])
        if not match:
            return None
        low_s, comma, high_s = match.group(1), match.group(2), match.group(3)
        low = int(low_s) if low_s else 0
        if not comma:
            return (low, low)
        if not high_s:
            return (low, _UNBOUNDED)
        return (low, int(high_s))
    return None


def _is_unbounded_quantifier(pattern_str: str, i: int) -> bool:
    """True when the quantifier at ``i`` is unbounded (*, +, {n,}, {,}).

    Bounded forms ``{n}``, ``{n,m}`` and ``{,m}`` are NOT unbounded.
    """
    bounds = _quantifier_bounds(pattern_str, i)
    return bounds is not None and bounds[1] == _UNBOUNDED


# A bounded OUTER repetition over a variable-length / alternating inner atom
# can still be catastrophic even though both bounds are finite:
# ``(a{1,2}){100}`` and ``(a?){50}`` both blow up on a few dozen characters.
# Small fixed outer counts (e.g. the ``{3}`` in an IPv4 matcher) keep the
# number of ambiguous splits constant-bounded and stay allowed.
MAX_SAFE_NESTED_REPEAT = 8


def _has_redos_risk(pattern_str: str) -> bool:
    """Static analysis for ReDoS-prone constructs in fallback patterns.

    Flags:
      - nested quantifiers: a quantified group whose inner atom is itself
        quantified with an UNBOUNDED repetition, or where the outer group
        quantifier is unbounded, e.g. ``(a+)+``, ``(a*)*``, ``(\\d{1,3})*`` —
        classic catastrophic backtracking shapes;
      - repetition over alternation groups, e.g. ``(a|aa)*`` where branches
        can consume overlapping input;
      - large BOUNDED repetition over a variable-count or alternating atom,
        e.g. ``(a{1,2}){100}`` or ``(a?){50}`` — finite bounds are not a
        guarantee of safety when the inner atom can match a variable number
        of the same characters.

    BOUNDED repetition of a FIXED atom is safe and accepted: ``(a|b){2}``
    and the IPv4 shape ``(\\d{1,3}\\.){3}\\d{1,3}`` do not blow up because the
    number of ambiguous splits stays constant-bounded.
    """
    # Stack frames: [quantified, unbounded, variable_count, saw_alternation]
    stack: List[List[bool]] = [[False, False, False, False]]
    i = 0
    n = len(pattern_str)

    while i < n:
        ch = pattern_str[i]

        if ch == "\\":
            i += 2
            continue

        if ch == "[":
            # Skip character class content; quantifiers inside are literals.
            j = i + 1
            if j < n and pattern_str[j] == "^":
                j += 1
            if j < n and pattern_str[j] == "]":
                j += 1
            while j < n and pattern_str[j] != "]":
                if pattern_str[j] == "\\":
                    j += 1
                j += 1
            i = j + 1
            continue

        if ch == "(":
            stack.append([False, False, False, False])
            i += 1
            continue

        if ch == ")":
            if len(stack) > 1:
                (
                    inner_quantified,
                    inner_unbounded,
                    inner_variable,
                    inner_alternation,
                ) = stack.pop()
                q_len = _match_quantifier(pattern_str, i + 1) or 0
                if q_len:
                    bounds = _quantifier_bounds(pattern_str, i + 1)
                    low, high = bounds if bounds is not None else (0, 0)
                    outer_unbounded = high == _UNBOUNDED
                    if inner_quantified and (inner_unbounded or outer_unbounded):
                        # Quantified atom inside a quantified group where at
                        # least one of the two repetitions is unbounded.
                        return True
                    if outer_unbounded and inner_alternation:
                        return True
                    # A large bounded outer repetition over a variable or
                    # alternating inner atom is exponential in disguise:
                    # (a{1,2}){100} has exponentially many ways to split its
                    # input. Fixed small counts stay constant-bounded.
                    if (
                        not outer_unbounded
                        and high > MAX_SAFE_NESTED_REPEAT
                        and (inner_variable or inner_alternation)
                    ):
                        return True
                    # The group plus its quantifier is a quantified atom from
                    # the parent's point of view. Propagate ambiguity upward
                    # (OR, not assignment) so deeper nesting is still caught.
                    stack[-1][0] = True
                    stack[-1][1] = (
                        stack[-1][1] or outer_unbounded or inner_unbounded
                    )
                    stack[-1][2] = (
                        stack[-1][2] or inner_variable or (low != high)
                    )
                    stack[-1][3] = stack[-1][3] or inner_alternation
                else:
                    # No outer quantifier: still carry the inner ambiguity up
                    # so an enclosing quantified group cannot hide it.
                    stack[-1][0] = stack[-1][0] or inner_quantified
                    stack[-1][1] = stack[-1][1] or inner_unbounded
                    stack[-1][2] = stack[-1][2] or inner_variable
                    stack[-1][3] = stack[-1][3] or inner_alternation
                i += 1 + q_len
                continue

        if ch == "|":
            stack[-1][3] = True
            i += 1
            continue

        q_len = _match_quantifier(pattern_str, i)
        if q_len:
            bounds = _quantifier_bounds(pattern_str, i)
            low, high = bounds if bounds is not None else (0, 0)
            stack[-1][0] = True
            stack[-1][1] = high == _UNBOUNDED
            stack[-1][2] = low != high
            i += q_len
            continue

        i += 1

    return False


_REJECTION_MESSAGE = (
    "rejected: not supported by RE2 (requires the backtracking fallback "
    "engine) and static analysis flagged nested/unbounded repetition "
    "(ReDoS risk). Simplify the pattern - e.g. avoid quantifying a group "
    "that already contains a quantifier or an alternation."
)


class RegexRule(BaseRule):
    """Rule that checks content against regex patterns."""

    rule_type = RuleType.REGEX

    def __init__(self, rule: Rule):
        super().__init__(rule)
        raw_patterns = self.config.get("patterns", [])
        self._patterns: List[Dict[str, Any]] = (
            raw_patterns if isinstance(raw_patterns, list) else []
        )
        self._compiled_patterns: List[tuple] = []
        # Compile-time budget: cap both the number of patterns and the total
        # characters fed to the regex compiler. A stored config with thousands
        # of patterns previously compiled them all on every worker start.
        compiled_chars = 0

        for pattern_config in self._patterns[:MAX_PATTERNS_PER_RULE]:
            if not isinstance(pattern_config, dict):
                continue
            pattern_str = pattern_config.get("pattern", "")
            if not pattern_str or not isinstance(pattern_str, str):
                continue

            try:
                # Length cap applies on every engine path.
                if len(pattern_str) > MAX_PATTERN_LENGTH:
                    raise ValueError(
                        f"pattern exceeds maximum length of {MAX_PATTERN_LENGTH}"
                    )
                if compiled_chars + len(pattern_str) > MAX_TOTAL_PATTERN_CHARS:
                    raise ValueError("pattern compile budget exhausted")

                # Get settings with defaults
                case_sensitive = pattern_config.get("case_sensitive", False)
                multiline = pattern_config.get("multiline", False)

                compiled = None
                if HAS_RE2 and re2_module:
                    # re2 uses an Options object for flags
                    options = re2_module.Options()
                    options.case_sensitive = case_sensitive
                    options.one_line = not multiline

                    try:
                        compiled = re2_module.compile(pattern_str, options=options)
                    except Exception as re2_error:
                        logger.debug(f"RE2 compile failed, falling back: {re2_error}")
                        # RE2 rejected the construct; static vetting below
                        # decides whether the fallback engine may be used.
                        pass

                if compiled is None:
                    if _has_redos_risk(pattern_str):
                        # Fallback engine + nested/unbounded repetition is a
                        # ReDoS surface: reject instead of silently compiling.
                        logger.error(
                            f"Rejected automod regex '{pattern_str}': {_REJECTION_MESSAGE}"
                        )
                        continue
                    flags = 0
                    if not case_sensitive:
                        flags |= re.IGNORECASE
                    if multiline:
                        flags |= re.MULTILINE
                    compiled = re.compile(pattern_str, flags)

                severity = self._parse_severity(
                    pattern_config.get("severity", "medium")
                )
                name = str(pattern_config.get("name", pattern_str[:30]))[
                    :MAX_PATTERN_NAME_LENGTH
                ]
                self._compiled_patterns.append((compiled, severity, name, pattern_str))
                compiled_chars += len(pattern_str)
                logger.debug(
                    f"Compiled automod regex '{pattern_str}' (case_sensitive={case_sensitive}, multiline={multiline})"
                )
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
                    logger.debug(
                        f"Regex rule '{self.rule.name}' matched pattern '{pattern_str}' in content from user {user_id}"
                    )
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

    def _parse_severity(self, sev_str: Any) -> ViolationSeverity:
        """Parse a severity string through the shared allowlist."""
        try:
            return coerce_severity(sev_str, ViolationSeverity.MEDIUM)
        except ValueError:
            return ViolationSeverity.MEDIUM

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
            if len(patterns) > MAX_PATTERNS_PER_RULE:
                issues.append(
                    f"patterns list exceeds maximum of {MAX_PATTERNS_PER_RULE}"
                )
            total_chars = sum(
                len(p.get("pattern", ""))
                for p in patterns
                if isinstance(p, dict) and isinstance(p.get("pattern"), str)
            )
            if total_chars > MAX_TOTAL_PATTERN_CHARS:
                issues.append(
                    f"patterns exceed total compile budget of {MAX_TOTAL_PATTERN_CHARS} chars"
                )
            for i, p in enumerate(patterns):
                if not isinstance(p, dict):
                    issues.append(f"pattern {i} must be a dictionary")
                    continue

                pattern_str = p.get("pattern")
                if not pattern_str:
                    issues.append(f"pattern {i} missing 'pattern' field")
                    continue
                if not isinstance(pattern_str, str):
                    issues.append(f"pattern {i} must be a string")
                    continue

                # Length cap applies on every engine path.
                if len(pattern_str) > MAX_PATTERN_LENGTH:
                    issues.append(
                        f"pattern {i} exceeds maximum length of {MAX_PATTERN_LENGTH}"
                    )
                    continue

                try:
                    if HAS_RE2 and re2_module:
                        try:
                            # re2 is naturally safe from ReDoS
                            re2_module.compile(pattern_str)
                        except Exception:
                            # RE2 cannot compile this construct (lookahead,
                            # backreference, ...). It would run on the
                            # backtracking fallback engine, so statically vet
                            # it for nested quantifiers / unbounded repetition
                            # groups and reject dangerous shapes explicitly.
                            if _has_redos_risk(pattern_str):
                                issues.append(f"pattern {i} {_REJECTION_MESSAGE}")
                            else:
                                re.compile(pattern_str)
                    else:
                        # No re2: every pattern runs on the backtracking
                        # engine, so the same static analysis applies.
                        if _has_redos_risk(pattern_str):
                            issues.append(f"pattern {i} {_REJECTION_MESSAGE}")
                        else:
                            re.compile(pattern_str)

                except Exception as e:
                    issues.append(f"pattern {i} invalid regex: {e}")

        return len(issues) == 0, issues
