from typing import Any, Dict, Optional

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
        return super()._get_timestamp()  # type: ignore[misc]

    def _generate_id(self) -> int:
        return super()._generate_id()  # type: ignore[misc]

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return super()._user_exists(user_id)  # type: ignore[misc]

    def _is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        return super()._is_member(server_id, user_id)  # type: ignore[misc]

    def _validate_server_name(self, name: str) -> str:
        return super()._validate_server_name(name)  # type: ignore[misc]

    def _cache_invalidate(self, prefix: str, key: Optional[Any] = None) -> None:
        super()._cache_invalidate(prefix, key)  # type: ignore[misc]

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
        super()._log_audit(  # type: ignore[reportAttributeAccessIssue]
            server_id, user_id, action, target_type, target_id, changes, reason
        )

    def get_server(self, server_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Any]:
        return super().get_server(server_id, user_id)  # type: ignore[misc]

    def get_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Any]:
        # Canonical (channel_id, user_id) order matches manager/base.py.
        return super().get_channel(channel_id, user_id)  # type: ignore[misc]

    def get_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Any]:
        return super().get_member(server_id, user_id)  # type: ignore[misc]

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        return super().get_permissions(user_id, server_id, channel_id)  # type: ignore[misc]

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        super().require_permission(user_id, server_id, permission, channel_id)  # type: ignore[misc]
