"""Hard, non-overridable resource bounds for poll fields.

The ``polls`` config block is deployment controlled. A typo (``-1``), a
hostile edit, or a config merge that widens a value must never turn a bounded
poll into an unbounded one, so every configured limit is routed through these
helpers before it is used. Values outside the outer envelope fall back to the
safe default rather than widening it; values inside it are honoured so an
operator may still tighten a deployment.

These constants are also imported by the API layer so request validation and
manager validation share one definition of "too big".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

# Outer envelope. These are not configurable: ``bound_count`` clamps any
# configured value into these ranges.
MIN_OPTIONS = 2
MAX_OPTIONS = 10
MIN_QUESTION_LENGTH = 1
MAX_QUESTION_LENGTH = 300
MIN_OPTION_LENGTH = 1
MAX_OPTION_LENGTH = 100
MIN_DURATION_HOURS = 1
MAX_DURATION_HOURS = 168
# Milliseconds since epoch; 4102444800000 is 2100-01-01. Anything beyond
# that is a corrupt/attacker-controlled clock, not a real close time.
MIN_TIMESTAMP_MS = 0
MAX_TIMESTAMP_MS = 4_102_444_800_000


def bound_count(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    """Coerce a configured count into ``[minimum, maximum]``.

    Uses the shared ``bounded_int`` primitive and fails safe: an unparsable or
    out-of-envelope value resolves to ``default`` instead of relaxing the
    bound the caller was trying to enforce.
    """
    try:
        return bounded_int(
            value, field, minimum=minimum, maximum=maximum, default=default
        )
    except HardeningViolation:
        return default


@dataclass(frozen=True)
class PollFieldLimits:
    """Resolved, envelope-bounded field limits for one config mapping."""

    min_options: int
    max_options: int
    max_question_length: int
    max_option_length: int
    min_duration_hours: int
    max_duration_hours: int


def resolve_field_limits(config: Mapping[str, Any]) -> PollFieldLimits:
    """Resolve every field limit from ``config`` inside its hard envelope."""
    min_options = bound_count(
        config.get("min_options", MIN_OPTIONS),
        "polls.min_options",
        minimum=MIN_OPTIONS,
        maximum=MAX_OPTIONS,
        default=MIN_OPTIONS,
    )
    max_options = bound_count(
        config.get("max_options", MAX_OPTIONS),
        "polls.max_options",
        minimum=MIN_OPTIONS,
        maximum=MAX_OPTIONS,
        default=MAX_OPTIONS,
    )
    # A misconfigured min greater than max must not invert the comparison and
    # reject every poll; collapse it to the (bounded) upper value.
    if min_options > max_options:
        min_options = max_options

    max_question_length = bound_count(
        config.get("max_question_length", MAX_QUESTION_LENGTH),
        "polls.max_question_length",
        minimum=MIN_QUESTION_LENGTH,
        maximum=MAX_QUESTION_LENGTH,
        default=MAX_QUESTION_LENGTH,
    )
    max_option_length = bound_count(
        config.get("max_option_length", MAX_OPTION_LENGTH),
        "polls.max_option_length",
        minimum=MIN_OPTION_LENGTH,
        maximum=MAX_OPTION_LENGTH,
        default=MAX_OPTION_LENGTH,
    )
    min_duration_hours = bound_count(
        config.get("min_duration_hours", MIN_DURATION_HOURS),
        "polls.min_duration_hours",
        minimum=MIN_DURATION_HOURS,
        maximum=MAX_DURATION_HOURS,
        default=MIN_DURATION_HOURS,
    )
    max_duration_hours = bound_count(
        config.get("max_duration_hours", MAX_DURATION_HOURS),
        "polls.max_duration_hours",
        minimum=MIN_DURATION_HOURS,
        maximum=MAX_DURATION_HOURS,
        default=MAX_DURATION_HOURS,
    )
    if min_duration_hours > max_duration_hours:
        min_duration_hours = max_duration_hours

    return PollFieldLimits(
        min_options=min_options,
        max_options=max_options,
        max_question_length=max_question_length,
        max_option_length=max_option_length,
        min_duration_hours=min_duration_hours,
        max_duration_hours=max_duration_hours,
    )


# A single vote may never select more options than the poll can carry. ten is
# the envelope ceiling for ``max_options``, so it is also the bulk ceiling.
MAX_VOTE_SELECTIONS = MAX_OPTIONS


def validate_option_ids(option_ids: Any) -> list[int]:
    """Parse, range-check, de-duplicate and cap a bulk vote selection.

    ``int(x)`` accepted negatives, oversized snowflakes and arbitrary-length
    lists; all three flowed into placeholder construction and INSERT loops.
    Every id now goes through the shared snowflake parser, selections longer
    than the envelope are refused, and duplicate ids collapse once (preserving
    the caller's order) so the ``IN (...)`` placeholder count stays bounded.
    """
    from .exceptions import InvalidPollOptionError

    if not isinstance(option_ids, (list, tuple)) or not option_ids:
        raise InvalidPollOptionError("At least one option must be selected")
    if len(option_ids) > MAX_VOTE_SELECTIONS:
        raise InvalidPollOptionError(
            f"At most {MAX_VOTE_SELECTIONS} options may be selected"
        )
    parsed: list[int] = []
    for raw in option_ids:
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise InvalidPollOptionError(f"Invalid option id: {raw!r}")
        try:
            parsed.append(parse_id(raw, "option_id"))
        except HardeningViolation as exc:
            raise InvalidPollOptionError(f"Invalid option id: {exc}") from exc
    return list(dict.fromkeys(parsed))


def validate_question_text(value: Any, config: Mapping[str, Any]) -> str:
    """Strip and length-check a question against envelope-bounded config.

    Centralising the check here means the manager and the API route enforce
    exactly the same rule, and the helper can be tested without a database.
    """
    from .exceptions import InvalidPollQuestionError

    text = str(value or "").strip()
    if not text:
        raise InvalidPollQuestionError("Poll question cannot be empty")
    # A question is stored verbatim and delivered to every viewer in the REST
    # response and gateway payloads. Control characters (CR/LF, NUL, DEL/C1,
    # Unicode line/paragraph separators) can forge a log line or a downstream
    # frame and are never legitimate in human-readable poll text, so refuse
    # them outright rather than relying on each client to parse around them.
    try:
        text = reject_control_characters(text, "poll question")
    except HardeningViolation as exc:
        raise InvalidPollQuestionError(
            "Poll question contains control characters"
        ) from exc
    max_len = resolve_field_limits(config).max_question_length
    if len(text) > max_len:
        raise InvalidPollQuestionError(
            f"Poll question cannot exceed {max_len} characters"
        )
    return text


def validate_option_text(value: Any, config: Mapping[str, Any]) -> str:
    """Strip and length-check one option against envelope-bounded config."""
    from .exceptions import InvalidPollOptionError

    text = str(value or "").strip()
    if not text:
        raise InvalidPollOptionError("Poll option cannot be empty")
    # Same rule as the question: option text is broadcast to every viewer, so
    # embedded control characters that could split a log line or WS frame are
    # refused instead of persisted.
    try:
        text = reject_control_characters(text, "poll option")
    except HardeningViolation as exc:
        raise InvalidPollOptionError(
            "Poll option contains control characters"
        ) from exc
    max_len = resolve_field_limits(config).max_option_length
    if len(text) > max_len:
        raise InvalidPollOptionError(f"Poll option cannot exceed {max_len} characters")
    return text


def bound_timestamp(value: Any, field: str = "timestamp") -> int:
    """Range-check an epoch-millisecond close/created time.

    A negative or absurd timestamp flows into ``ends_at`` arithmetic and
    comparisons, so it is rejected rather than trusted.
    """
    return bounded_int(
        value,
        field,
        minimum=MIN_TIMESTAMP_MS,
        maximum=MAX_TIMESTAMP_MS,
    )


def bound_option_count(options: Any, config: Mapping[str, Any]) -> int:
    """Return the validated option count, or raise ``PollOptionLimitError``."""
    from .exceptions import PollOptionLimitError

    limits = resolve_field_limits(config)
    count = len(options) if isinstance(options, (list, tuple)) else -1
    if count < limits.min_options or count > limits.max_options:
        raise PollOptionLimitError(
            f"Poll must have between {limits.min_options} and "
            f"{limits.max_options} options",
            limits.min_options,
            limits.max_options,
            count,
        )
    return count


__all__ = [
    "MAX_OPTIONS",
    "MAX_DURATION_HOURS",
    "MAX_OPTION_LENGTH",
    "MAX_TIMESTAMP_MS",
    "MIN_DURATION_HOURS",
    "MIN_TIMESTAMP_MS",
    "MAX_VOTE_SELECTIONS",
    "MAX_QUESTION_LENGTH",
    "MIN_OPTIONS",
    "PollFieldLimits",
    "bound_count",
    "bound_timestamp",
    "bound_option_count",
    "validate_option_ids",
    "resolve_field_limits",
    "validate_option_text",
    "validate_question_text",
]
