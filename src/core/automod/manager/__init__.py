"""
AutoMod manager - Core business logic for auto-moderation.

Handles rule evaluation, action execution, and integration with other modules.
"""

import json
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
import utils.validator as validator
from src.core.base import BaseManager, SnowflakeID

from ..models import (
    Rule,
    RuleType,
    RuleAction,
    RuleMatch,
    ActionType,
    Violation,
    ViolationSeverity,
    AuditEntry,
    UserReputation,
    Exemption,
    CheckResult,
    AICheckResult,
    BulkScanResult,
)
from ..exceptions import (
    RuleNotFoundError,
    RuleValidationError,
    ExemptionError,
)
from ..rules import (
    KeywordRule,
    RegexRule,
    MessageSpamRule,
    MentionSpamRule,
    InviteLinkRule,
    ExternalLinkRule,
    CapsPercentageRule,
    MassEmojiRule,
    RepeatedCharsRule,
    AIModerationRule,
)
from ..actions import (
    DeleteMessageAction,
    TimeoutUserAction,
    KickUserAction,
    BanUserAction,
    AlertModeratorsAction,
)
from ..ai import OpenAIAdapter, PerspectiveAdapter, CustomAdapter


class AutoModManager(BaseManager):
    """Core auto-moderation manager."""

    RULE_CLASSES = {
        RuleType.KEYWORD: KeywordRule,
        RuleType.REGEX: RegexRule,
        RuleType.MESSAGE_SPAM: MessageSpamRule,
        RuleType.MENTION_SPAM: MentionSpamRule,
        RuleType.INVITE_LINKS: InviteLinkRule,
        RuleType.EXTERNAL_LINKS: ExternalLinkRule,
        RuleType.CAPS_PERCENTAGE: CapsPercentageRule,
        RuleType.MASS_EMOJI: MassEmojiRule,
        RuleType.REPEATED_CHARS: RepeatedCharsRule,
        RuleType.AI_MODERATION: AIModerationRule,
    }

    ACTION_CLASSES = {
        ActionType.DELETE_MESSAGE: DeleteMessageAction,
        ActionType.TIMEOUT_USER: TimeoutUserAction,
        ActionType.KICK_USER: KickUserAction,
        ActionType.BAN_USER: BanUserAction,
        ActionType.ALERT_MODERATORS: AlertModeratorsAction,
    }

    def __init__(
        self, db, servers_module=None, messaging_module=None, notifications_module=None
    ):
        """
        Initialize the AutoMod manager.

        Args:
            db: Database instance
            servers_module: Servers module for kicks/bans
            messaging_module: Messaging module for message operations
            notifications_module: Notifications module for alerts
        """
        super().__init__(db)
        self._servers = servers_module
        self._messaging = messaging_module
        self._notifications = notifications_module
        self._config = self._load_config()
        self._ai_adapters = {}

        self._init_ai_adapters()

        logger.info("AutoMod module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load automod configuration."""
        defaults = {
            "enabled": True,
            "default_actions": ["delete_message", "alert_moderators"],
            "rate_limit_window": 60,
            "reputation_decay_rate": 1.0,
            "reputation_decay_interval": 86400,
            "max_violations_before_action": 1,
        }

        automod_config = config.get("automod", {})
        return {**defaults, **automod_config}

    def _init_ai_adapters(self):
        """Initialize configured AI adapters."""
        ai_config = self._config.get("ai", {})

        if ai_config.get("openai", {}).get("api_key"):
            self._ai_adapters["openai"] = OpenAIAdapter(ai_config["openai"])

        if ai_config.get("perspective", {}).get("api_key"):
            self._ai_adapters["perspective"] = PerspectiveAdapter(
                ai_config["perspective"]
            )

        if ai_config.get("custom", {}).get("endpoint_url"):
            self._ai_adapters["custom"] = CustomAdapter(ai_config["custom"])

    def reload_config(self) -> None:
        self._config = self._load_config()
        self._ai_adapters = {}
        self._init_ai_adapters()

    def check_message(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_id: SnowflakeID,
        content: str,
        message_id: Optional[SnowflakeID] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CheckResult:
        """
        Check a message against all enabled rules.

        Args:
            server_id: Server where message was sent
            channel_id: Channel where message was sent
            user_id: User who sent the message
            content: Message content
            message_id: Optional message ID
            context: Additional context

        Returns:
            CheckResult with violations and actions
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
        """Evaluate a single rule against content."""
        rule_class = self.RULE_CLASSES.get(rule.rule_type)

        if not rule_class:
            return RuleMatch(rule_id=rule.id, rule_type=rule.rule_type, matched=False)

        rule_instance = rule_class(rule)
        return rule_instance.check(content, user_id, channel_id, context)

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
        """
        Process a violation and execute actions.

        Args:
            server_id: Server ID
            channel_id: Channel ID
            user_id: User ID
            message_id: Message ID
            match: Rule match result
            actions: Actions to execute
            context: Additional context

        Returns:
            Created Violation record
        """
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

        metadata = dict(match.match_details or {})
        metadata["rate_tracking"] = {
            "count": rate_count,
            "window_start": window_start,
            "rate_limit_window": int(self._config.get("rate_limit_window", 60)),
            "max_violations_before_action": max_violations,
        }
        if suppressed_actions:
            metadata["rate_tracking"]["actions_suppressed"] = suppressed_actions

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

    def _get_rate_window_ms(self) -> int:
        window_seconds = int(self._config.get("rate_limit_window", 60))
        if window_seconds <= 0:
            return 60000
        return window_seconds * 1000

    def _increment_rate_tracking(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        rule_type: str,
        now: int,
    ) -> tuple:
        window_ms = self._get_rate_window_ms()
        window_start = now - (now % window_ms)

        row = self._db.fetch_one(
            """SELECT id, count FROM automod_rate_tracking
               WHERE server_id = ? AND user_id = ? AND rule_type = ? AND window_start = ?""",
            (server_id, user_id, rule_type, window_start),
        )

        if row:
            new_count = int(row["count"]) + 1
            self._db.execute(
                "UPDATE automod_rate_tracking SET count = ? WHERE id = ?",
                (new_count, row["id"]),
            )
            return new_count, window_start

        rate_id = self._generate_id()
        self._db.execute(
            """INSERT INTO automod_rate_tracking
               (id, server_id, user_id, rule_type, window_start, count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rate_id, server_id, user_id, rule_type, window_start, 1),
        )
        return 1, window_start

    def _execute_action(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]],
    ) -> bool:
        """Execute a single action."""
        action_class = self.ACTION_CLASSES.get(action.action_type)

        if not action_class:
            logger.warning(f"Unknown action type: {action.action_type}")
            return False

        executor = action_class(
            self._db, self._servers, self._messaging, self._notifications
        )

        can_execute, reason = executor.can_execute(action, violation, context)
        if not can_execute:
            logger.debug(f"Cannot execute {action.action_type.value}: {reason}")
            return False

        return executor.execute(action, violation, context)

    def create_rule(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        rule_type: RuleType,
        rule_config: Dict[str, Any],
        actions: List[Dict[str, Any]],
        applied_roles: Optional[List[SnowflakeID]] = None,
        exempt_roles: Optional[List[SnowflakeID]] = None,
        exempt_channels: Optional[List[SnowflakeID]] = None,
        priority: int = 0,
        check_all: bool = False,
    ) -> Rule:
        """
        Create a new automod rule.

        Args:
            user_id: User creating the rule
            server_id: Server for the rule
            name: Rule name
            rule_type: Type of rule
            rule_config: Rule-specific configuration
            actions: List of actions to take
            applied_roles: Roles this rule applies to (None/Empty = everyone)
            exempt_roles: Roles exempt from this rule
            exempt_channels: Channels exempt from this rule
            priority: Rule priority (higher = checked first)
            check_all: Whether to continue checking after match

        Returns:
            Created Rule
        """
        rule_class = self.RULE_CLASSES.get(rule_type)
        if rule_class:
            valid, issues = rule_class.validate_config(rule_config)
            if not valid:
                raise RuleValidationError("Invalid rule configuration", issues)

        parsed_actions = []
        for action_data in actions:
            action_type_str = action_data.get("action_type", action_data.get("type"))
            try:
                action_type = ActionType(action_type_str)
            except ValueError:
                raise RuleValidationError(f"Invalid action type: {action_type_str}")

            parsed_actions.append(
                RuleAction(
                    action_type=action_type,
                    duration_seconds=action_data.get("duration_seconds"),
                    reason=action_data.get("reason"),
                    notify_user=action_data.get("notify_user", True),
                    metadata=action_data.get("metadata", {}),
                )
            )

        now = self._get_timestamp()
        rule_id = self._generate_id()

        actions_json = json.dumps(
            [
                {
                    "action_type": a.action_type.value,
                    "duration_seconds": a.duration_seconds,
                    "reason": a.reason,
                    "notify_user": a.notify_user,
                    "metadata": a.metadata,
                }
                for a in parsed_actions
            ]
        )

        self._db.execute(
            """INSERT INTO automod_rules 
               (id, server_id, name, rule_type, enabled, config, actions,
                applied_roles, exempt_roles, exempt_channels, priority, check_all, created_at, updated_at, created_by)
               VALUES (?, ?, ?, ?, CAST(? AS INTEGER), ?, ?, ?, ?, ?, CAST(? AS INTEGER), CAST(? AS INTEGER), ?, ?, ?)""",
            (
                rule_id,
                server_id,
                name,
                rule_type.value,
                1,
                json.dumps(rule_config),
                actions_json,
                json.dumps(applied_roles or []),
                json.dumps(exempt_roles or []),
                json.dumps(exempt_channels or []),
                priority,
                1 if check_all else 0,
                now,
                now,
                user_id,
            ),
        )

        logger.debug(f"Created automod rule {rule_id} for server {server_id}")

        result = self.get_rule(rule_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_rule(self, rule_id: SnowflakeID) -> Optional[Rule]:
        """Get a rule by ID."""
        row = self._db.fetch_one("SELECT * FROM automod_rules WHERE id = ?", (rule_id,))

        if not row:
            return None

        return self._row_to_rule(row)

    def update_rule(
        self,
        user_id: SnowflakeID,
        rule_id: SnowflakeID,
        name: Optional[str] = None,
        rule_config: Optional[Dict[str, Any]] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        applied_roles: Optional[List[SnowflakeID]] = None,
        exempt_roles: Optional[List[SnowflakeID]] = None,
        exempt_channels: Optional[List[SnowflakeID]] = None,
        priority: Optional[int] = None,
        check_all: Optional[bool] = None,
    ) -> Rule:
        """Update an existing rule."""
        rule = self.get_rule(rule_id)
        if not rule:
            raise RuleNotFoundError("Rule not found")

        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)

        if rule_config is not None:
            rule_class = self.RULE_CLASSES.get(rule.rule_type)
            if rule_class:
                valid, issues = rule_class.validate_config(rule_config)
                if not valid:
                    raise RuleValidationError("Invalid rule configuration", issues)
            updates.append("config = ?")
            params.append(json.dumps(rule_config))

        if actions is not None:
            parsed_actions = []
            for action_data in actions:
                action_type_str = action_data.get(
                    "action_type", action_data.get("type")
                )
                try:
                    action_type = ActionType(action_type_str)
                except ValueError:
                    raise RuleValidationError(f"Invalid action type: {action_type_str}")
                parsed_actions.append(
                    {
                        "action_type": action_type.value,
                        "duration_seconds": action_data.get("duration_seconds"),
                        "reason": action_data.get("reason"),
                        "notify_user": action_data.get("notify_user", True),
                        "metadata": action_data.get("metadata", {}),
                    }
                )
            updates.append("actions = ?")
            params.append(json.dumps(parsed_actions))

        if applied_roles is not None:
            updates.append("applied_roles = ?")
            params.append(json.dumps(applied_roles))

        if exempt_roles is not None:
            updates.append("exempt_roles = ?")
            params.append(json.dumps(exempt_roles))

        if exempt_channels is not None:
            updates.append("exempt_channels = ?")
            params.append(json.dumps(exempt_channels))

        if priority is not None:
            updates.append("priority = CAST(? AS INTEGER)")
            params.append(priority)

        if check_all is not None:
            updates.append("check_all = CAST(? AS INTEGER)")
            params.append(1 if check_all else 0)

        if updates:
            updates.append("updated_at = CAST(? AS BIGINT)")
            params.append(self._get_timestamp())
            params.append(rule_id)

            self._db.execute(
                f"UPDATE automod_rules SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

        result = self.get_rule(rule_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_rule(self, user_id: SnowflakeID, rule_id: SnowflakeID) -> bool:
        """Delete a rule."""
        rule = self.get_rule(rule_id)
        if not rule:
            raise RuleNotFoundError("Rule not found")

        self._db.execute("DELETE FROM automod_rules WHERE id = ?", (rule_id,))
        self._db.execute("DELETE FROM automod_exemptions WHERE rule_id = ?", (rule_id,))

        logger.debug(f"Deleted automod rule {rule_id}")
        return True

    def get_server_rules(self, server_id: SnowflakeID) -> List[Rule]:
        """Get all rules for a server."""
        result = self._get_server_rules(server_id, enabled_only=False)
        return result

    def _get_server_rules(
        self, server_id: SnowflakeID, enabled_only: bool = False
    ) -> List[Rule]:
        """Internal method to get server rules."""
        query = "SELECT * FROM automod_rules WHERE server_id = ?"
        params = [server_id]

        if enabled_only:
            query += " AND enabled = 1"

        query += " ORDER BY priority DESC"

        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_rule(row) for row in rows]

    def set_rule_enabled(
        self, user_id: SnowflakeID, rule_id: SnowflakeID, enabled: bool
    ) -> Rule:
        """Enable or disable a rule."""
        rule = self.get_rule(rule_id)
        if not rule:
            raise RuleNotFoundError("Rule not found")

        self._db.execute(
            "UPDATE automod_rules SET enabled = CAST(? AS INTEGER), updated_at = CAST(? AS BIGINT) WHERE id = ?",
            (1 if enabled else 0, self._get_timestamp(), rule_id),
        )

        result = self.get_rule(rule_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def add_exemption(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
        rule_id: Optional[SnowflakeID] = None,
    ) -> Exemption:
        """
        Add an exemption from automod rules.

        Args:
            user_id: User adding the exemption
            server_id: Server ID
            target_type: "role" or "channel"
            target_id: Role or channel ID
            rule_id: Specific rule to exempt from (None = all rules)

        Returns:
            Created Exemption
        """
        if target_type not in ["role", "channel"]:
            raise ExemptionError("target_type must be 'role' or 'channel'")

        existing = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = ? AND target_id = ? AND rule_id IS ?""",
            (server_id, target_type, target_id, rule_id),
        )

        if existing:
            raise ExemptionError("Exemption already exists")

        now = self._get_timestamp()
        exemption_id = self._generate_id()

        self._db.execute(
            """INSERT INTO automod_exemptions 
               (id, server_id, rule_id, target_type, target_id, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (exemption_id, server_id, rule_id, target_type, target_id, now, user_id),
        )

        return Exemption(
            id=exemption_id,
            server_id=server_id,
            rule_id=rule_id,
            target_type=target_type,
            target_id=target_id,
            created_at=now,
            created_by=user_id,
        )

    def remove_exemption(self, user_id: SnowflakeID, exemption_id: SnowflakeID) -> bool:
        """Remove an exemption."""
        existing = self._db.fetch_one(
            "SELECT id FROM automod_exemptions WHERE id = ?", (exemption_id,)
        )

        if not existing:
            raise ExemptionError("Exemption not found")

        self._db.execute("DELETE FROM automod_exemptions WHERE id = ?", (exemption_id,))
        return True

    def _is_exempt(
        self, server_id: SnowflakeID, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        """Check if user/channel is globally exempt."""
        # 0. Server owner is always exempt
        server_row = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        if server_row and int(server_row["owner_id"]) == int(user_id):
            return True

        # 1. Global Channel Exemption
        channel_exempt = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = 'channel' AND target_id = ? AND rule_id IS NULL""",
            (server_id, channel_id),
        )
        if channel_exempt:
            logger.debug(
                f"Channel {channel_id} is exempt from AutoMod in server {server_id}"
            )
            return True

        # Fetch user roles once for multiple checks
        user_roles = self._db.fetch_all(
            """SELECT role_id FROM srv_member_roles 
               JOIN srv_members ON srv_member_roles.member_id = srv_members.id
               WHERE srv_members.server_id = ? AND srv_members.user_id = ?""",
            (server_id, user_id),
        )
        role_ids = [r["role_id"] for r in user_roles]

        # 1.5 Automatic exemption: any role with administrator permission
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            role_rows = self._db.fetch_all(
                f"SELECT permissions FROM srv_roles WHERE id IN ({placeholders}) AND deleted = 0",
                tuple(role_ids),
            )
            for rr in role_rows:
                try:
                    perms = json.loads(rr.get("permissions") or "{}")
                except Exception:
                    perms = {}
                if isinstance(perms, dict) and perms.get("administrator") is True:
                    return True

        # 2. Global Role Exemption
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            role_exempt = self._db.fetch_one(
                f"""SELECT id FROM automod_exemptions
                    WHERE server_id = ? AND target_type = 'role' AND target_id IN ({placeholders}) AND rule_id IS NULL""",
                (server_id, *role_ids),
            )
            if role_exempt:
                logger.debug(
                    f"User {user_id} has a role exempt from AutoMod in server {server_id}"
                )
                return True

        return False

    def _is_exempt_from_rule(
        self, rule: Rule, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        """Check if user/channel is exempt from a specific rule."""
        # 1. Channel exemption
        if channel_id in rule.exempt_channels:
            return True

        user_roles = self._db.fetch_all(
            """SELECT role_id FROM srv_member_roles 
               JOIN srv_members ON srv_member_roles.member_id = srv_members.id
               WHERE srv_members.server_id = ? AND srv_members.user_id = ?""",
            (rule.server_id, user_id),
        )
        user_role_ids = {r["role_id"] for r in user_roles}

        # 2. Rule-specific Role Exemption
        if user_role_ids & set(rule.exempt_roles):
            return True

        # 3. Rule-specific applied roles (if not empty, MUST have one of these roles)
        if rule.applied_roles:
            if not (user_role_ids & set(rule.applied_roles)):
                # If the rule has a target list, and user doesn't have any of them, they are exempt
                return True

        # 4. Manual exemptions in DB
        channel_exempt = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = 'channel' AND target_id = ? AND rule_id = ?""",
            (rule.server_id, channel_id, rule.id),
        )
        if channel_exempt:
            return True

        if user_role_ids:
            placeholders = ",".join("?" * len(user_role_ids))
            role_exempt = self._db.fetch_one(
                f"""SELECT id FROM automod_exemptions
                    WHERE server_id = ? AND target_type = 'role' AND target_id IN ({placeholders}) AND rule_id = ?""",
                (rule.server_id, *user_role_ids, rule.id),
            )
            if role_exempt:
                return True

        return False

    def get_violations(
        self,
        server_id: SnowflakeID,
        user_id: Optional[SnowflakeID] = None,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[Violation]:
        """Get violations for a server."""
        query = "SELECT * FROM automod_violations WHERE server_id = ?"
        params = [server_id]

        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        if before_id is not None:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(limit, 100))

        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_violation(row) for row in rows]

    def get_user_reputation(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> UserReputation:
        """Get user's reputation score for a server."""
        row = self._db.fetch_one(
            "SELECT * FROM automod_reputation WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )

        if not row:
            now = self._get_timestamp()
            return UserReputation(
                user_id=user_id,
                server_id=server_id,
                score=100.0,
                violation_count=0,
                last_violation_at=None,
                last_decay_at=now,
                created_at=now,
                updated_at=now,
            )

        return self._row_to_reputation(row)

    def _update_reputation(
        self, user_id: SnowflakeID, server_id: SnowflakeID, severity: ViolationSeverity
    ):
        """Update user reputation after a violation."""
        penalty_map = {
            ViolationSeverity.LOW: 5,
            ViolationSeverity.MEDIUM: 10,
            ViolationSeverity.HIGH: 20,
            ViolationSeverity.CRITICAL: 40,
        }
        penalty = penalty_map.get(severity, 10)

        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT * FROM automod_reputation WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )

        if existing:
            new_score = max(0, existing["score"] - penalty)
            self._db.execute(
                """UPDATE automod_reputation 
                   SET score = ?, violation_count = violation_count + 1, 
                       last_violation_at = ?, updated_at = ?
                   WHERE user_id = ? AND server_id = ?""",
                (new_score, now, now, user_id, server_id),
            )
        else:
            self._db.execute(
                """INSERT INTO automod_reputation 
                   (user_id, server_id, score, violation_count, last_violation_at, 
                    last_decay_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, 100 - penalty, 1, now, now, now, now),
            )

    def decay_reputation(self, server_id: Optional[SnowflakeID] = None) -> int:
        """
        Apply reputation decay to restore scores over time.

        Args:
            server_id: Optional server to decay (None = all servers)

        Returns:
            Number of users updated
        """
        decay_rate = self._config.get("reputation_decay_rate", 1.0)
        decay_interval = self._config.get("reputation_decay_interval", 86400) * 1000

        now = self._get_timestamp()
        cutoff = now - decay_interval

        query = """
            UPDATE automod_reputation 
            SET score = MIN(100, score + ?), last_decay_at = ?, updated_at = ?
            WHERE last_decay_at < ? AND score < 100
        """
        params = [decay_rate, now, now, cutoff]

        if server_id is not None:
            query = query.replace("WHERE", "WHERE server_id = ? AND")
            params.insert(0, server_id)

        self._db.execute(query, tuple(params))

        result = self._db.fetch_one("SELECT changes() as count")
        count = result["count"] if result else 0
        return count

    def get_audit_log(
        self,
        server_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        action_type: Optional[ActionType] = None,
    ) -> List[AuditEntry]:
        """Get automod audit log entries."""
        query = "SELECT * FROM automod_audit WHERE server_id = ?"
        params: List[Any] = [server_id]

        if action_type is not None:
            query += " AND action_type = ?"
            params.append(action_type.value)

        if before_id is not None:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(limit, 100))

        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_audit_entry(row) for row in rows]

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
        """
        Manually trigger an automod action.

        Args:
            user_id: Moderator triggering the action
            server_id: Server ID
            target_user_id: User to take action against
            action_type: Type of action
            reason: Reason for action
            duration_seconds: Duration for timeout
            context: Additional context

        Returns:
            True if action was executed
        """
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

    def scan_messages_bulk(
        self,
        server_id: SnowflakeID,
        channel_id: SnowflakeID,
        message_ids: List[SnowflakeID],
        context: Optional[Dict[str, Any]] = None,
    ) -> BulkScanResult:
        """
        Scan multiple messages for violations (raid detection).

        Args:
            server_id: Server ID
            channel_id: Channel ID
            message_ids: List of message IDs to scan
            context: Additional context

        Returns:
            BulkScanResult with scan results
        """
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

    def check_user(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> Dict[str, Any]:
        """
        Get user's automod status for a server.

        Args:
            user_id: User ID
            server_id: Server ID

        Returns:
            Dict with reputation, violation count, etc.
        """
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

    def check_ai(
        self,
        content: str,
        backend: str = "openai",
        context: Optional[Dict[str, Any]] = None,
    ) -> AICheckResult:
        """
        Check content using AI moderation backend.

        Args:
            content: Content to check
            backend: AI backend to use
            context: Additional context

        Returns:
            AICheckResult from the backend
        """
        adapter = self._ai_adapters.get(backend)

        if not adapter:
            from ..exceptions import AIBackendUnavailableError

            raise AIBackendUnavailableError(
                f"AI backend '{backend}' not configured", backend=backend
            )

        return adapter.check_content(content, context)

    def ensure_default_rules(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """
        Ensure standard moderation rules exist for a server.

        This creates baseline protection against spam and hate speech.
        """
        existing = self._get_server_rules(server_id)
        if existing:
            return

        # 1. Anti-Spam Rule
        self.create_rule(
            user_id=user_id,
            server_id=server_id,
            name="Anti-Spam",
            rule_type=RuleType.MESSAGE_SPAM,
            rule_config={
                "max_messages": 5,
                "window_seconds": 10,
                "duplicate_threshold": 3,
                "similarity_threshold": 0.9,
            },
            actions=[
                {"type": "delete_message"},
                {"type": "alert_moderators", "reason": "Automated spam detection"},
            ],
            priority=100,
        )

        # 2. Hate Speech Filter (Keyword based)
        self.create_rule(
            user_id=user_id,
            server_id=server_id,
            name="Hate Speech Filter (Keywords)",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["nigger", "faggot", "kike", "chink", "retard", "paki"],
                "whole_word": True,
                "case_sensitive": False,
            },
            actions=[
                {"type": "delete_message"},
                {
                    "type": "timeout_user",
                    "duration_seconds": 3600,
                    "reason": "Hate speech is not allowed",
                },
            ],
            priority=200,
        )

        # 3. Hate Speech Filter (Regex for obfuscation)
        self.create_rule(
            user_id=user_id,
            server_id=server_id,
            name="Hate Speech Filter (Regex)",
            rule_type=RuleType.REGEX,
            rule_config={
                "patterns": [
                    {
                        "name": "Slur Obfuscation",
                        "pattern": r"n[i1l][gq]{2}[e3]r|f[a@][gq]{2}[o0]t|k[i1]k[e3]|ch[i1]nk",
                        "case_sensitive": False,
                        "severity": "critical",
                    }
                ]
            },
            actions=[
                {"type": "delete_message"},
                {
                    "type": "timeout_user",
                    "duration_seconds": 3600,
                    "reason": "Hate speech obfuscation is not allowed",
                },
            ],
            priority=210,
        )

    def _row_to_rule(self, row) -> Rule:
        """Convert database row to Rule."""
        actions_data = json.loads(row["actions"])
        actions = [
            RuleAction(
                action_type=ActionType(a["action_type"]),
                duration_seconds=a.get("duration_seconds"),
                reason=a.get("reason"),
                notify_user=a.get("notify_user", True),
                metadata=a.get("metadata", {}),
            )
            for a in actions_data
        ]

        return Rule(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            rule_type=RuleType(row["rule_type"]),
            enabled=bool(row["enabled"]),
            config=json.loads(row["config"]),
            actions=actions,
            applied_roles=json.loads(row["applied_roles"])
            if "applied_roles" in row
            else [],
            exempt_roles=json.loads(row["exempt_roles"]),
            exempt_channels=json.loads(row["exempt_channels"]),
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by=row["created_by"],
            check_all=bool(row["check_all"]),
        )

    def _row_to_violation(self, row) -> Violation:
        """Convert database row to Violation."""
        return Violation(
            id=row["id"],
            server_id=row["server_id"],
            channel_id=row["channel_id"],
            user_id=row["user_id"],
            message_id=row["message_id"],
            rule_id=row["rule_id"],
            rule_type=RuleType(row["rule_type"]),
            matched_content=row["matched_content"],
            actions_taken=[ActionType(a) for a in json.loads(row["actions_taken"])],
            severity=ViolationSeverity(row["severity"]),
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def _row_to_reputation(self, row) -> UserReputation:
        """Convert database row to UserReputation."""
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

    def _row_to_audit_entry(self, row) -> AuditEntry:
        """Convert database row to AuditEntry."""
        return AuditEntry(
            id=row["id"],
            server_id=row["server_id"],
            action_type=ActionType(row["action_type"]),
            target_user_id=row["target_user_id"],
            moderator_id=row["moderator_id"],
            rule_id=row["rule_id"],
            reason=row["reason"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
        )
