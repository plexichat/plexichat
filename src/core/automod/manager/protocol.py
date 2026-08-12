from typing import Any, Dict, List, Tuple, cast

from src.core.base import SnowflakeID
from ..models import Rule, ActionType, RuleType, ViolationSeverity, UserReputation


class AutoModProtocol:
    _db: Any = None
    _servers: Any = None
    _messaging: Any = None
    _notifications: Any = None
    _config: Dict[str, Any] = {}
    _ai_adapters: Dict[str, Any] = {}

    RULE_CLASSES: Dict[RuleType, Any] = {}
    ACTION_CLASSES: Dict[ActionType, Any] = {}

    def _get_timestamp(self) -> int:
        return cast(AutoModProtocol, super())._get_timestamp()

    def _generate_id(self) -> int:
        return cast(AutoModProtocol, super())._generate_id()

    def _get_server_rules(
        self, server_id: SnowflakeID, enabled_only: bool = False
    ) -> List[Rule]:
        return cast(AutoModProtocol, super())._get_server_rules(server_id, enabled_only)

    def _is_exempt(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> bool:
        return cast(AutoModProtocol, super())._is_exempt(server_id, user_id, channel_id)

    def _is_exempt_from_rule(
        self,
        rule: Rule,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> bool:
        return cast(AutoModProtocol, super())._is_exempt_from_rule(
            rule, user_id, channel_id
        )

    def _increment_rate_tracking(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        rule_type: str,
        now: int,
    ) -> Tuple[int, int]:
        return cast(AutoModProtocol, super())._increment_rate_tracking(
            server_id, user_id, rule_type, now
        )

    def get_user_reputation(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> UserReputation:
        return cast(AutoModProtocol, super()).get_user_reputation(user_id, server_id)

    def _update_reputation(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        severity: ViolationSeverity,
    ) -> None:
        cast(AutoModProtocol, super())._update_reputation(user_id, server_id, severity)
