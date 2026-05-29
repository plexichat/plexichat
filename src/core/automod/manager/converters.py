import json
from typing import Any

from ..models import (
    Rule,
    RuleType,
    RuleAction,
    ActionType,
    Violation,
    ViolationSeverity,
    UserReputation,
    AuditEntry,
)


def safe_json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def row_to_rule(row) -> Rule:
    actions_data = safe_json_loads(row["actions"], []) or []
    actions = [
        RuleAction(
            action_type=ActionType(a["action_type"]),
            duration_seconds=a.get("duration_seconds"),
            reason=a.get("reason"),
            notify_user=a.get("notify_user", True),
            metadata=a.get("metadata", {}),
        )
        for a in actions_data
        if isinstance(a, dict) and "action_type" in a
    ]

    return Rule(
        id=row["id"],
        server_id=row["server_id"],
        name=row["name"],
        rule_type=RuleType(row["rule_type"]),
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
        rule_type=RuleType(row["rule_type"]),
        matched_content=row["matched_content"],
        actions_taken=[ActionType(a) for a in actions_taken_raw if isinstance(a, str)],
        severity=ViolationSeverity(row["severity"]),
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
        action_type=ActionType(row["action_type"]),
        target_user_id=row["target_user_id"],
        moderator_id=row["moderator_id"],
        rule_id=row["rule_id"],
        reason=row["reason"],
        metadata=safe_json_loads(row.get("metadata"), {}),
        created_at=row["created_at"],
    )
