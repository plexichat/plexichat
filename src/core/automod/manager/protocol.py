from typing import Any, Dict, List, Optional, Tuple, cast

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

    def _authorize_automod_mutation(
        self, server_id: SnowflakeID, user_id: Optional[SnowflakeID]
    ) -> None:
        """Defense-in-depth server-scope authorization for rule mutations.

        Route handlers already enforce ``server.automod``; this guard protects
        any caller that reaches the manager layer directly. Skipped when no
        caller identity or no servers module is available.
        """
        if user_id is None or server_id is None or self._servers is None:
            return

        require = getattr(self._servers, "require_permission", None)
        if require is None:
            return

        try:
            require(user_id, server_id, "server.automod")
        except Exception as exc:
            from src.core.servers.exceptions import (
                PermissionDeniedError as _SrvPermError,
            )

            if isinstance(exc, _SrvPermError):
                from ..exceptions import PermissionDeniedError

                raise PermissionDeniedError(
                    f"Caller {user_id} lacks 'server.automod' permission in server {server_id}"
                ) from exc
            raise
