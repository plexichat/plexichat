from typing import Optional, List, Dict, Any

import utils.logger as logger
import utils.validator as validator
from src.core.base import SnowflakeID
from ..models import (
    Rule,
    RuleMatch,
    RuleType,
    ActionType,
    CheckResult,
    AICheckResult,
    BulkScanResult,
)


from ..rules.compiled_cache import CompiledRuleCache
from .protocol import AutoModProtocol

# Chunk ``IN`` binds so SQLite's default 999-variable cap is never approached;
# Postgres accepts far more but chunking is harmless there.
_BULK_LOOKUP_CHUNK = 500


class EvaluationMixin(AutoModProtocol):
    def check_message(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_id: SnowflakeID,
        content: str,
        message_id: Optional[SnowflakeID] = None,
        context: Optional[Dict[str, Any]] = None,
        rule_scope: str = "all",
    ) -> CheckResult:
        """Evaluate rules against a message.

        ``rule_scope`` controls which rules run:
          - ``"all"``: every enabled rule (legacy behaviour).
          - ``"fast"``: every rule except AI moderation. Used synchronously
            before a message is broadcast so cheap rules can block the send.
          - ``"ai"``: only AI-moderation rules. Used asynchronously after a
            message has been broadcast.
        """
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

        if rule_scope == "fast":
            rules = [r for r in rules if r.rule_type != RuleType.AI_MODERATION]
        elif rule_scope == "ai":
            rules = [r for r in rules if r.rule_type == RuleType.AI_MODERATION]

        logger.debug(
            f"Checking {len(rules)} rules (scope={rule_scope}) for server {server_id}"
        )

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

    def check_message_fast(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_id: SnowflakeID,
        content: str,
        message_id: Optional[SnowflakeID] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CheckResult:
        """Run only the cheap (non-AI) rules. Synchronous, used pre-send."""
        return self.check_message(
            server_id,
            channel_id,
            user_id,
            content,
            message_id,
            context,
            rule_scope="fast",
        )

    def check_ai_rules(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_id: SnowflakeID,
        content: str,
        message_id: Optional[SnowflakeID] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CheckResult:
        """Run only the AI-moderation rules. Used async after broadcast."""
        return self.check_message(
            server_id,
            channel_id,
            user_id,
            content,
            message_id,
            context,
            rule_scope="ai",
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

        # Compiling a rule (regex patterns, keyword canonicalization, domain
        # sets, ...) depends only on the rule content, never on the message.
        # Reuse the compiled instance across messages instead of rebuilding it
        # for every check. The cache is keyed by rule content/revision, so an
        # edited rule is recompiled on its next evaluation.
        cache = self._get_compiled_rule_cache()
        rule_instance = cache.get(rule, rule_class)
        return rule_instance.check(content, user_id, channel_id, context)

    def _get_compiled_rule_cache(self) -> CompiledRuleCache:
        """Return this manager's compiled-rule cache, creating it on demand.

        Lazily initialized so any construction path (including tests that
        bypass ``AutoModManager.__init__``) gets the optimization without
        extra wiring.
        """
        cache = getattr(self, "_compiled_rules", None)
        if cache is None:
            cache = CompiledRuleCache()
            self._compiled_rules = cache
        return cache

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

        # Batch the message fetch: one chunked ``IN`` query replaces the old
        # per-id SELECT (an N+1 read that issued a round trip for every id).
        # Rows are keyed by id and consumed in the caller's original order so
        # ordering and duplicate ids behave exactly as before. The
        # ``deleted = 0`` predicate is preserved and missing ids simply have
        # no entry (they are skipped), keeping the scan fail-closed.
        messages_by_id: Dict[SnowflakeID, Dict[str, Any]] = {}
        for start in range(0, len(message_ids), _BULK_LOOKUP_CHUNK):
            chunk = message_ids[start : start + _BULK_LOOKUP_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                f"SELECT * FROM msg_messages WHERE deleted = 0 AND id IN ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                messages_by_id[row["id"]] = row

        for message_id in message_ids:
            msg = messages_by_id.get(message_id)
            if msg is None:
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
        backend: str = "shieldstral",
        context: Optional[Dict[str, Any]] = None,
    ) -> AICheckResult:
        adapter = self._ai_adapters.get(backend)

        if not adapter:
            from ..exceptions import AIBackendUnavailableError

            raise AIBackendUnavailableError(
                f"AI backend '{backend}' not configured", backend=backend
            )

        return adapter.check_content(content, context)
