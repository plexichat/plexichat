"""Resource ceilings for automod rule configuration.

Rule configs are admin-controlled but still untrusted input: they are stored
as JSON, reloaded on every process start and used to build matchers. These
bounds keep a single rule from consuming unbounded memory or compile time.
"""

# A rule carrying hundreds of patterns forces hundreds of regex compiles on
# every worker restart; 32 is well above any legitimate moderation policy.
MAX_PATTERNS_PER_RULE = 32
# Total pattern characters compiled for one rule (RE2 and fallback alike).
MAX_TOTAL_PATTERN_CHARS = 8_000
# Pattern names are copied into violation details and log lines.
MAX_PATTERN_NAME_LENGTH = 128
# Repeated-char rules interpolate max_repeats into a regex quantifier; an
# unbounded value produces a huge quantifier at construction time.
MAX_REPEATS = 100
# Invite-link code length is interpolated into a regex quantifier as well.
MAX_CODE_LENGTH = 64
# Ceiling for the caps rule's ``min_length``; a larger value only makes the
# rule fire on every message, so it is bounded like the other config knobs.
MAX_MIN_LENGTH = 10_000
# A single message can carry thousands of emoji, and the emoji rule walks
# every one of them on each message; 1_000 is far above any legitimate post.
MAX_EMOJI_COUNT = 1_000
# Keyword list / per-keyword ceilings.
MAX_KEYWORDS = 500
MAX_KEYWORD_LENGTH = 256
MAX_SEVERITY_MAP_ENTRIES = 500
MAX_LISTED_DOMAINS = 1_000
MAX_DOMAIN_LENGTH = 253
# Manual/action reason length copied into the audit table and log lines.
MAX_REASON_LENGTH = 500
# Timeouts/actions driven by config integers.
MAX_DURATION_SECONDS = 2_592_000  # 30 days


def bounded_reason(value, field="reason"):
    """Return a control-character-free reason within MAX_REASON_LENGTH."""
    from src.core.security.hardening import (
        HardeningViolation,
        reject_control_characters,
    )

    try:
        text = reject_control_characters(value, field)
    except HardeningViolation:
        raise
    if len(text) > MAX_REASON_LENGTH:
        raise ValueError(f"{field} exceeds {MAX_REASON_LENGTH} characters")
    return text


def clip_text(value, maximum, field="text"):
    """Clip untrusted text before it is stored in a violation/audit row.

    The text is quoted from a *violating user's* message (matched keywords,
    regex captures, examples, ...) and lands in ``automod_violations`` and the
    ``automod_audit.reason`` column, which moderators and log shippers render as
    single-line entries.  A local C0+DEL-only regex let U+0085 (NEL), the
    Unicode LINE/PARAGRAPH SEPARATORs (U+2028/U+2029) and the bidi format
    controls through, so one crafted message could forge extra audit lines or
    render as a different string than the one actually recorded.  Use the shared
    primitive so every consumer stays on the same side of that parser
    differential; it removes the whole forbidden set, never less than before.
    """
    from src.core.security.hardening.text import strip_control_characters

    text = strip_control_characters(value)
    return text[:maximum]
