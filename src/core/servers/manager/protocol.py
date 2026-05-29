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

    def _get_timestamp(self) -> int: ...
    def _generate_id(self) -> int: ...
    def _user_exists(self, user_id: SnowflakeID) -> bool: ...

    def _is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool: ...

    def _validate_server_name(self, name: str) -> str: ...

    def _cache_invalidate(self, prefix: str, key: Optional[Any] = None) -> None: ...

    def _log_audit(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[SnowflakeID] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None: ...

    def get_server(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Any]: ...

    def get_channel(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Optional[Any]: ...

    def get_member(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Any]: ...

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]: ...

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None: ...
