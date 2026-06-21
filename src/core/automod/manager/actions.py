import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from ..models import (
    RuleAction,
    ActionType,
    RuleMatch,
    Violation,
    ViolationSeverity,
    RuleType,
)


from .protocol import AutoModProtocol


class ActionMixin(AutoModProtocol):
    def process_violation(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_id: SnowflakeID,
        message_id: Optional[SnowflakeID],
        match: RuleMatch,
        actions: List[RuleAction],
        context: Optional[Dict[str, Any]] = None,
    ) -> Violation:
        # SECURITY: ban/kick/delete action executors REFUSE the raw-SQL
        # fallback unless ``context[\"__system_context__\"]`` is set
        # to True. AutoMod is fundamentally a SYSTEM actor
        # (it executes on behalf of moderation rules, not an
        # individual user's request), so we mark any caller-supplied
        # context as system context at the entry point. This
        # prevents the previous behaviour where AutoMod silently
        # failed every auto-ban because nobody set the flag.
        if context is None:
            context = {}
        context.setdefault("__system_context__", True)
        context.setdefault("__automod_caller__", True)
        now = self._get_timestamp()
        violation_id = self._generate_id()

        rate_count, window_start = self._increment_rate_tracking(
            server_id, user_id, match.rule_type.value, now
        )
        max_violations = int(self._config.get("max_violations_before_action", 1))
        should_execute = rate_count >= max_violations

        actions_taken = []
        suppressed_actions = []

        for action in actions:
            if action.action_type == ActionType.LOG_ONLY:
                actions_taken.append(ActionType.LOG_ONLY)
                continue
            if not should_execute:
                suppressed_actions.append(action.action_type.value)
                continue

            success = self._execute_action(
                action,
                Violation(
                    id=violation_id,
                    server_id=server_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    message_id=message_id,
                    rule_id=match.rule_id,
                    rule_type=match.rule_type,
                    matched_content=match.matched_content or "",
                    actions_taken=[],
                    severity=match.severity,
                    created_at=now,
                ),
                context,
            )

            if success:
                actions_taken.append(action.action_type)

        metadata: Dict[str, Any] = dict(match.match_details or {})
        rate_tracking: Dict[str, Any] = {
            "count": rate_count,
            "window_start": window_start,
            "rate_limit_window": int(self._config.get("rate_limit_window", 60)),
            "max_violations_before_action": max_violations,
        }
        if suppressed_actions:
            rate_tracking["actions_suppressed"] = suppressed_actions
        metadata["rate_tracking"] = rate_tracking

        self._db.execute(
            """INSERT INTO automod_violations 
               (id, server_id, channel_id, user_id, message_id, rule_id, rule_type,
                matched_content, actions_taken, severity, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                violation_id,
                server_id,
                channel_id,
                user_id,
                message_id,
                match.rule_id,
                match.rule_type.value,
                match.matched_content or "",
                json.dumps([a.value for a in actions_taken]),
                match.severity.value,
                json.dumps(metadata),
                now,
            ),
        )

        self._update_reputation(user_id, server_id, match.severity)

        return Violation(
            id=violation_id,
            server_id=server_id,
            channel_id=channel_id,
            user_id=user_id,
            message_id=message_id,
            rule_id=match.rule_id,
            rule_type=match.rule_type,
            matched_content=match.matched_content or "",
            actions_taken=actions_taken,
            severity=match.severity,
            created_at=now,
            metadata=metadata,
        )

    def _execute_action(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]],
    ) -> bool:
        action_class = self.ACTION_CLASSES.get(action.action_type)

        if not action_class:
            logger.warning(f"Unknown action type: {action.action_type}")
            return False

        executor = action_class(
            self._db,
            self._servers,
            self._messaging,
            self._notifications,
        )

        can_execute, reason = executor.can_execute(action, violation, context)
        if not can_execute:
            logger.debug(f"Cannot execute {action.action_type.value}: {reason}")
            return False

        return executor.execute(action, violation, context)

    def trigger_action(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        target_user_id: SnowflakeID,
        action_type: ActionType,
        reason: str,
        duration_seconds: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        action = RuleAction(
            action_type=action_type, duration_seconds=duration_seconds, reason=reason
        )

        now = self._get_timestamp()
        violation = Violation(
            id=self._generate_id(),
            server_id=server_id,
            channel_id=0,
            user_id=target_user_id,
            message_id=None,
            rule_id=0,
            rule_type=RuleType.KEYWORD,
            matched_content="Manual action",
            actions_taken=[],
            severity=ViolationSeverity.MEDIUM,
            created_at=now,
        )

        if action_type == ActionType.LOG_ONLY:
            success = True
        else:
            # SECURITY: ad-hoc moderator-triggered ``trigger_action``
            # calls (admin CLI buttons, manual bans, audit re-walks)
            # also need the system context flag set so the
            # ban/kick/delete executors do not refuse the call.
            if context is None:
                context = {}
            context.setdefault("__system_context__", True)
            success = self._execute_action(action, violation, context)

        if success:
            audit_id = self._generate_id()
            self._db.execute(
                """INSERT INTO automod_audit 
                   (id, server_id, action_type, target_user_id, moderator_id, rule_id, reason, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    audit_id,
                    server_id,
                    action_type.value,
                    target_user_id,
                    user_id,
                    None,
                    reason,
                    json.dumps({"manual": True}),
                    now,
                ),
            )

        return success
