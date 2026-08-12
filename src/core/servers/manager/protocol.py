from typing import Any, Dict, Optional, cast

from src.core.base import SnowflakeID
from ..models import AuditLogAction


class ServerProtocol:
    _db: Any = None
    _auth: Any = None
    _messaging: Any = None
    _config: Dict[str, Any] = {}
    _encrypt_descriptions: bool = False

    _cache_ttl: int = 60
    _member_cache_prefix: str = "srv_member:"
    _permission_cache_prefix: str = "srv_permission:"
    _channel_cache_prefix: str = "srv_channel:"
    _server_owner_cache_prefix: str = "srv_owner:"
    _member_roles_cache_prefix: str = "srv_member_roles:"

    role_handler: Any = None
    member_handler: Any = None
    channel_handler: Any = None
    audit_handler: Any = None

    def _get_timestamp(self) -> int:
        return cast(ServerProtocol, super())._get_timestamp()

    def _generate_id(self) -> int:
        return cast(ServerProtocol, super())._generate_id()

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return cast(ServerProtocol, super())._user_exists(user_id)

    def _is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        return cast(ServerProtocol, super())._is_member(server_id, user_id)

    def _validate_server_name(self, name: str) -> str:
        return cast(ServerProtocol, super())._validate_server_name(name)

    def _cache_invalidate(self, prefix: str, key: Optional[Any] = None) -> None:
        cast(ServerProtocol, super())._cache_invalidate(prefix, key)

    def _log_audit(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[SnowflakeID] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        cast(ServerProtocol, super())._log_audit(
            server_id, user_id, action, target_type, target_id, changes, reason
        )

    def get_server(self, server_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Any]:
        return cast(ServerProtocol, super()).get_server(server_id, user_id)

    def get_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Any]:
        # Canonical (channel_id, user_id) order matches manager/base.py.
        return cast(ServerProtocol, super()).get_channel(channel_id, user_id)

    def get_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Any]:
        return cast(ServerProtocol, super()).get_member(server_id, user_id)

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        return cast(ServerProtocol, super()).get_permissions(
            user_id, server_id, channel_id
        )

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        cast(ServerProtocol, super()).require_permission(
            user_id, server_id, permission, channel_id
        )
