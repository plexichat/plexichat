from typing import Optional, List, Dict, Any

import utils.logger as logger
import utils.validator as validator
from src.core.base import SnowflakeID
from ..models import (
    Rule,
    RuleMatch,
    ActionType,
    CheckResult,
    AICheckResult,
    BulkScanResult,
)


from .protocol import AutoModProtocol


class EvaluationMixin(AutoModProtocol):
    def check_message(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_id: SnowflakeID,
        content: str,
        message_id: Optional[SnowflakeID] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CheckResult:
        logger.debug(f"AutoMod checking message from {user_id} in server {server_id}")
        if not self._config.get("enabled", True):
            return CheckResult(passed=True, violations=[], actions_to_take=[])

        validation_result = validator.validate(content)
        if not validation_result.is_valid:
            logger.warning(
                f"Message from user {user_id} failed validation: {validation_result.error_message}"
            )

        if self._is_exempt(server_id, user_id, channel_id):
            logger.debug(f"User {user_id} is exempt from AutoMod in server {server_id}")
            return CheckResult(passed=True, violations=[], actions_to_take=[])

        rules = self._get_server_rules(server_id, enabled_only=True)
        rules.sort(key=lambda r: r.priority, reverse=True)

        logger.debug(f"Checking {len(rules)} rules for server {server_id}")

        violations = []
        actions_to_take = []
        context = dict(context or {})
        context.setdefault("automod_manager", self)

        for rule in rules:
            if self._is_exempt_from_rule(rule, user_id, channel_id):
                logger.debug(f"User {user_id} is exempt from rule '{rule.name}'")
                continue

            match = self._evaluate_rule(rule, content, user_id, channel_id, context)

            if match.matched:
                logger.info(
                    f"AutoMod: Rule '{rule.name}' matched content from user {user_id} in server {server_id}"
                )
                violations.append(match)
                actions_to_take.extend(rule.actions)

                if not rule.check_all:
                    break

        if not violations:
            return CheckResult(passed=True, violations=[], actions_to_take=[])

        should_delete = any(
            a.action_type == ActionType.DELETE_MESSAGE for a in actions_to_take
        )
        should_timeout = any(
            a.action_type == ActionType.TIMEOUT_USER for a in actions_to_take
        )
        timeout_duration = None

        for action in actions_to_take:
            if (
                action.action_type == ActionType.TIMEOUT_USER
                and action.duration_seconds
            ):
                timeout_duration = action.duration_seconds
                break

        return CheckResult(
            passed=False,
            violations=violations,
            actions_to_take=actions_to_take,
            should_delete=should_delete,
            should_timeout=should_timeout,
            timeout_duration=timeout_duration,
        )

    def _evaluate_rule(
        self,
        rule: Rule,
        content: str,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        context: Dict[str, Any],
    ) -> RuleMatch:
        rule_class = self.RULE_CLASSES.get(rule.rule_type)

        if not rule_class:
            return RuleMatch(rule_id=rule.id, rule_type=rule.rule_type, matched=False)

        rule_instance = rule_class(rule)
        return rule_instance.check(content, user_id, channel_id, context)

    def check_user(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> Dict[str, Any]:
        reputation = self.get_user_reputation(user_id, server_id)

        recent_violations = self._db.fetch_one(
            """SELECT COUNT(*) as count FROM automod_violations 
               WHERE user_id = ? AND server_id = ? AND created_at > ?""",
            (user_id, server_id, self._get_timestamp() - 86400000),
        )

        return {
            "user_id": user_id,
            "server_id": server_id,
            "reputation_score": reputation.score,
            "total_violations": reputation.violation_count,
            "recent_violations_24h": recent_violations["count"]
            if recent_violations
            else 0,
            "last_violation_at": reputation.last_violation_at,
            "is_exempt": self._is_exempt(server_id, user_id, 0),
        }

    def scan_messages_bulk(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        message_ids: List[SnowflakeID],
        context: Optional[Dict[str, Any]] = None,
    ) -> BulkScanResult:
        start_time = self._get_timestamp()

        messages_flagged = []
        user_violations = {}

        for message_id in message_ids:
            msg = self._db.fetch_one(
                "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
            )

            if not msg:
                continue

            content = msg.get("content", "")
            user_id = msg["author_id"]

            result = self.check_message(
                server_id=server_id,
                channel_id=channel_id,
                user_id=user_id,
                content=content,
                message_id=message_id,
                context=context,
            )

            if not result.passed:
                messages_flagged.append(message_id)
                user_violations[user_id] = user_violations.get(user_id, 0) + 1

        end_time = self._get_timestamp()

        return BulkScanResult(
            total_scanned=len(message_ids),
            violations_found=len(messages_flagged),
            messages_flagged=messages_flagged,
            user_violations=user_violations,
            scan_duration_ms=end_time - start_time,
        )

    def check_ai(
        self,
        content: str,
        backend: str = "openai",
        context: Optional[Dict[str, Any]] = None,
    ) -> AICheckResult:
        adapter = self._ai_adapters.get(backend)

        if not adapter:
            from ..exceptions import AIBackendUnavailableError

            raise AIBackendUnavailableError(
                f"AI backend '{backend}' not configured", backend=backend
            )

        return adapter.check_content(content, context)
