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
