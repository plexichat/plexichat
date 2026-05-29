import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from ..models import Rule, RuleType, RuleAction, ActionType
from ..exceptions import RuleNotFoundError, RuleValidationError
from .converters import row_to_rule
from .protocol import AutoModProtocol


class RuleOpsMixin(AutoModProtocol):
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
        _silent: bool = False,
    ) -> Rule:
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

        if not _silent:
            logger.debug(f"Created automod rule {rule_id} for server {server_id}")

        result = self.get_rule(rule_id)
        assert result is not None
        return result

    def get_rule(self, rule_id: SnowflakeID) -> Optional[Rule]:
        row = self._db.fetch_one("SELECT * FROM automod_rules WHERE id = ?", (rule_id,))

        if not row:
            return None

        return row_to_rule(row)

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
        assert result is not None
        return result

    def delete_rule(self, user_id: SnowflakeID, rule_id: SnowflakeID) -> bool:
        rule = self.get_rule(rule_id)
        if not rule:
            raise RuleNotFoundError("Rule not found")

        self._db.execute("DELETE FROM automod_rules WHERE id = ?", (rule_id,))
        self._db.execute("DELETE FROM automod_exemptions WHERE rule_id = ?", (rule_id,))

        logger.debug(f"Deleted automod rule {rule_id}")
        return True

    def get_server_rules(self, server_id: SnowflakeID) -> List[Rule]:
        result = self._get_server_rules(server_id, enabled_only=False)
        return result

    def _get_server_rules(
        self, server_id: SnowflakeID, enabled_only: bool = False
    ) -> List[Rule]:
        query = "SELECT * FROM automod_rules WHERE server_id = ?"
        params = [server_id]

        if enabled_only:
            query += " AND enabled = 1"

        query += " ORDER BY priority DESC"

        rows = self._db.fetch_all(query, tuple(params))
        return [row_to_rule(row) for row in rows]

    def set_rule_enabled(
        self, user_id: SnowflakeID, rule_id: SnowflakeID, enabled: bool
    ) -> Rule:
        rule = self.get_rule(rule_id)
        if not rule:
            raise RuleNotFoundError("Rule not found")

        self._db.execute(
            "UPDATE automod_rules SET enabled = CAST(? AS INTEGER), updated_at = CAST(? AS BIGINT) WHERE id = ?",
            (1 if enabled else 0, self._get_timestamp(), rule_id),
        )

        result = self.get_rule(rule_id)
        assert result is not None
        return result

    def ensure_default_rules(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        existing = self._get_server_rules(server_id)
        if existing:
            return

        rules_created = 0

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
            _silent=True,
        )
        rules_created += 1

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
            _silent=True,
        )
        rules_created += 1

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
            _silent=True,
        )
        rules_created += 1

        logger.debug(
            "Created %d default automod rules for server %d",
            rules_created,
            server_id,
        )
