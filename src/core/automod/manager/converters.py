import json
from typing import Any

from src.core.security.hardening import HardeningViolation, bounded_int

from ..limits import MAX_DURATION_SECONDS
from ..models import (
    Rule,
    RuleAction,
    Violation,
    UserReputation,
    AuditEntry,
    ViolationSeverity,
    coerce_action_type,
    coerce_rule_type,
    coerce_severity,
)


def safe_json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _allowlisted_or_none(coerce, value: Any):
    """Coerce a stored enum value, returning ``None`` when it is unknown.

    A rule's ``actions`` / ``actions_taken`` columns are JSON written by an
    older or third-party client. One bogus entry used to raise from the Enum
    constructor and make ``get_rule()`` / the whole audit listing unusable;
    with the allowlist helpers an unknown value is simply skipped.
    """
    try:
        return coerce(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _bounded_duration(value: Any):
    """Re-apply the write-time duration bound to a stored rule action.

    Actions are persisted as JSON and may originate from a legacy or tampered
    row, so create/update validation alone is not enough. Drop invalid or
    out-of-range values (the executor then falls back to its safe default)
    instead of letting an unbounded/negative/fractional timeout reach it.
    """
    if value is None:
        return None
    try:
        return bounded_int(
            value, "duration_seconds", minimum=0, maximum=MAX_DURATION_SECONDS
        )
    except (HardeningViolation, ValueError, TypeError):
        return None


def row_to_rule(row) -> Rule:
    actions_data = safe_json_loads(row["actions"], []) or []
    actions = []
    for entry in actions_data:
        if not isinstance(entry, dict) or "action_type" not in entry:
            continue
        action_type = _allowlisted_or_none(coerce_action_type, entry["action_type"])
        if action_type is None:
            continue
        actions.append(
            RuleAction(
                action_type=action_type,
                duration_seconds=_bounded_duration(entry.get("duration_seconds")),
                reason=entry.get("reason"),
                notify_user=entry.get("notify_user", True),
                metadata=entry.get("metadata", {}),
            )
        )

    return Rule(
        id=row["id"],
        server_id=row["server_id"],
        name=row["name"],
        rule_type=coerce_rule_type(row["rule_type"]),
        enabled=bool(row["enabled"]),
        config=safe_json_loads(row["config"], {}),
        actions=actions,
        applied_roles=safe_json_loads(row.get("applied_roles"), []),
        exempt_roles=safe_json_loads(row["exempt_roles"], []),
        exempt_channels=safe_json_loads(row["exempt_channels"], []),
        priority=row["priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        check_all=bool(row["check_all"]),
    )


def row_to_violation(row) -> Violation:
    actions_taken_raw = safe_json_loads(row.get("actions_taken"), []) or []
    return Violation(
        id=row["id"],
        server_id=row["server_id"],
        channel_id=row["channel_id"],
        user_id=row["user_id"],
        message_id=row["message_id"],
        rule_id=row["rule_id"],
        rule_type=coerce_rule_type(row["rule_type"]),
        matched_content=row["matched_content"],
        actions_taken=[
            coerced
            for a in actions_taken_raw
            if isinstance(a, str)
            and (coerced := _allowlisted_or_none(coerce_action_type, a)) is not None
        ],
        severity=_allowlisted_or_none(coerce_severity, row["severity"])
        or ViolationSeverity.MEDIUM,
        created_at=row["created_at"],
        metadata=safe_json_loads(row.get("metadata"), {}),
    )


def row_to_reputation(row) -> UserReputation:
    return UserReputation(
        user_id=row["user_id"],
        server_id=row["server_id"],
        score=row["score"],
        violation_count=row["violation_count"],
        last_violation_at=row["last_violation_at"],
        last_decay_at=row["last_decay_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_audit_entry(row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        server_id=row["server_id"],
        action_type=coerce_action_type(row["action_type"]),
        target_user_id=row["target_user_id"],
        moderator_id=row["moderator_id"],
        rule_id=row["rule_id"],
        reason=row["reason"],
        metadata=safe_json_loads(row.get("metadata"), {}),
        created_at=row["created_at"],
    )
