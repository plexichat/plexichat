from typing import Any, Dict, Optional, Tuple


class AvatarProtocol:
    _db: Any = None
    _setup_complete: bool = False

    def _get_timestamp(self) -> int:
        return super()._get_timestamp()  # type: ignore[misc]

    def _generate_id(self) -> int:
        return super()._generate_id()  # type: ignore[misc]

    def _get_db(self):
        return super()._get_db()  # type: ignore[misc]

    def _cache_binary(self, key: str, data: bytes, ttl: int = 3600) -> None:
        super()._cache_binary(key, data, ttl)  # type: ignore[misc]

    def _get_cached_binary(self, key: str) -> Optional[bytes]:
        return super()._get_cached_binary(key)  # type: ignore[misc]

    def _delete_cached_binary(self, key: str) -> None:
        super()._delete_cached_binary(key)  # type: ignore[misc]

    def _get_config(self, key: str, default: Any = None) -> Any:
        return super()._get_config(key, default)  # type: ignore[misc]

    def _get_max_size(self) -> int:
        return super()._get_max_size()  # type: ignore[misc]

    def _get_max_file_size(self) -> int:
        return super()._get_max_file_size()  # type: ignore[misc]

    def _get_allowed_types(self) -> list:
        return super()._get_allowed_types()  # type: ignore[misc]

    def _validate_content_type(self, content_type: str) -> bool:
        return super()._validate_content_type(content_type)  # type: ignore[misc]

    def _detect_content_type(self, image_data: bytes, fallback: str) -> str:
        return super()._detect_content_type(image_data, fallback)  # type: ignore[misc]

    def _process_image(
        self, image_data: bytes, content_type: str
    ) -> Tuple[bytes, int, int, bool]:
        return super()._process_image(image_data, content_type)  # type: ignore[misc]

    def _compute_checksum(self, data: bytes) -> str:
        return super()._compute_checksum(data)  # type: ignore[misc]

    def create_tables(self, db: Any) -> None:
        super().create_tables(db)  # type: ignore[misc]

    def upload_user_avatar(
        self, user_id: int, image_data: bytes, content_type: str
    ) -> Dict[str, Any]:
        return super().upload_user_avatar(user_id, image_data, content_type)  # type: ignore[misc]

    def get_user_avatar(self, user_id: int) -> Optional[Dict[str, Any]]:
        return super().get_user_avatar(user_id)  # type: ignore[misc]

    def get_user_avatar_data(self, user_id: int) -> Optional[Tuple[bytes, str, str]]:
        return super().get_user_avatar_data(user_id)  # type: ignore[misc]

    def get_user_avatar_url(self, user_id: int) -> Optional[str]:
        return super().get_user_avatar_url(user_id)  # type: ignore[misc]

    def get_user_avatar_checksum(self, user_id: int) -> Optional[str]:
        return super().get_user_avatar_checksum(user_id)  # type: ignore[misc]

    def delete_user_avatar(self, user_id: int) -> bool:
        return super().delete_user_avatar(user_id)  # type: ignore[misc]

    def upload_server_icon(
        self, server_id: int, image_data: bytes, content_type: str
    ) -> Dict[str, Any]:
        return super().upload_server_icon(server_id, image_data, content_type)  # type: ignore[misc]

    def get_server_icon(self, server_id: int) -> Optional[Dict[str, Any]]:
        return super().get_server_icon(server_id)  # type: ignore[misc]

    def get_server_icon_data(self, server_id: int) -> Optional[Tuple[bytes, str, str]]:
        return super().get_server_icon_data(server_id)  # type: ignore[misc]

    def get_server_icon_url(self, server_id: int) -> Optional[str]:
        return super().get_server_icon_url(server_id)  # type: ignore[misc]

    def get_server_icon_checksum(self, server_id: int) -> Optional[str]:
        return super().get_server_icon_checksum(server_id)  # type: ignore[misc]

    def delete_server_icon(self, server_id: int) -> bool:
        return super().delete_server_icon(server_id)  # type: ignore[misc]

    def generate_default_svg(self, seed: Any, initials: str) -> str:
        return super().generate_default_svg(seed, initials)  # type: ignore[misc]
