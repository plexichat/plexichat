import time
from typing import Any, Dict, Optional, Tuple, cast

from src.utils.encryption import generate_snowflake_id


class AvatarProtocol:
    _db: Any = None
    _setup_complete: bool = False

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _get_db(self):
        return cast(AvatarProtocol, super())._get_db()

    def _cache_binary(self, key: str, data: bytes, ttl: int = 3600) -> None:
        cast(AvatarProtocol, super())._cache_binary(key, data, ttl)

    def _get_cached_binary(self, key: str) -> Optional[bytes]:
        return cast(AvatarProtocol, super())._get_cached_binary(key)

    def _delete_cached_binary(self, key: str) -> None:
        cast(AvatarProtocol, super())._delete_cached_binary(key)

    def _get_config(self, key: str, default: Any = None) -> Any:
        return cast(AvatarProtocol, super())._get_config(key, default)

    def _get_max_size(self) -> int:
        return cast(AvatarProtocol, super())._get_max_size()

    def _get_max_file_size(self) -> int:
        return cast(AvatarProtocol, super())._get_max_file_size()

    def _get_allowed_types(self) -> list:
        return cast(AvatarProtocol, super())._get_allowed_types()

    def _validate_content_type(self, content_type: str) -> bool:
        return cast(AvatarProtocol, super())._validate_content_type(content_type)

    def _detect_content_type(self, image_data: bytes, fallback: str) -> str:
        return cast(AvatarProtocol, super())._detect_content_type(image_data, fallback)

    def _process_image(
        self, image_data: bytes, content_type: str
    ) -> Tuple[bytes, int, int, bool]:
        return cast(AvatarProtocol, super())._process_image(image_data, content_type)

    def _compute_checksum(self, data: bytes) -> str:
        return cast(AvatarProtocol, super())._compute_checksum(data)

    def create_tables(self, db: Any) -> None:
        cast(AvatarProtocol, super()).create_tables(db)

    def upload_user_avatar(
        self, user_id: int, image_data: bytes, content_type: str
    ) -> Dict[str, Any]:
        return cast(AvatarProtocol, super()).upload_user_avatar(
            user_id, image_data, content_type
        )

    def get_user_avatar(self, user_id: int) -> Optional[Dict[str, Any]]:
        return cast(AvatarProtocol, super()).get_user_avatar(user_id)

    def get_user_avatar_data(self, user_id: int) -> Optional[Tuple[bytes, str, str]]:
        return cast(AvatarProtocol, super()).get_user_avatar_data(user_id)

    def get_user_avatar_url(self, user_id: int) -> Optional[str]:
        return cast(AvatarProtocol, super()).get_user_avatar_url(user_id)

    def get_user_avatar_checksum(self, user_id: int) -> Optional[str]:
        return cast(AvatarProtocol, super()).get_user_avatar_checksum(user_id)

    def delete_user_avatar(self, user_id: int) -> bool:
        return cast(AvatarProtocol, super()).delete_user_avatar(user_id)

    def upload_server_icon(
        self, server_id: int, image_data: bytes, content_type: str
    ) -> Dict[str, Any]:
        return cast(AvatarProtocol, super()).upload_server_icon(
            server_id, image_data, content_type
        )

    def get_server_icon(self, server_id: int) -> Optional[Dict[str, Any]]:
        return cast(AvatarProtocol, super()).get_server_icon(server_id)

    def get_server_icon_data(self, server_id: int) -> Optional[Tuple[bytes, str, str]]:
        return cast(AvatarProtocol, super()).get_server_icon_data(server_id)

    def get_server_icon_url(self, server_id: int) -> Optional[str]:
        return cast(AvatarProtocol, super()).get_server_icon_url(server_id)

    def get_server_icon_checksum(self, server_id: int) -> Optional[str]:
        return cast(AvatarProtocol, super()).get_server_icon_checksum(server_id)

    def delete_server_icon(self, server_id: int) -> bool:
        return cast(AvatarProtocol, super()).delete_server_icon(server_id)

    def generate_default_svg(self, seed: Any, initials: str) -> str:
        return cast(AvatarProtocol, super()).generate_default_svg(seed, initials)
