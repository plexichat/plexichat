from typing import Any, Dict, List, Tuple

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

    def _get_timestamp(self) -> int: ...
    def _generate_id(self) -> int: ...

    def _get_server_rules(
        self, server_id: SnowflakeID, enabled_only: bool = False
    ) -> List[Rule]: ...

    def _is_exempt(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> bool: ...

    def _is_exempt_from_rule(
        self,
        rule: Rule,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> bool: ...

    def _increment_rate_tracking(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        rule_type: str,
        now: int,
    ) -> Tuple[int, int]: ...

    def get_user_reputation(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> UserReputation: ...

    def _update_reputation(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        severity: ViolationSeverity,
    ) -> None: ...
