from typing import Any, Optional

from ..models import Thread, ThreadMember


class ThreadProtocol:
    _db: Any = None
    _auth: Any = None
    _messaging: Any = None
    _servers: Any = None
    _notifications: Any = None
    _encrypt_thread_names: bool = False

    _check_permission: Any = None
    _require_permission: Any = None

    _is_member: Any = None

    def _get_timestamp(self) -> int:
        return super()._get_timestamp()  # type: ignore[misc]

    def _generate_id(self) -> int:
        return super()._generate_id()  # type: ignore[misc]

    def can_view_thread(self, user_id: int, thread_id: int) -> bool:
        return super().can_view_thread(user_id, thread_id)  # type: ignore[misc]

    def can_send_in_thread(self, user_id: int, thread_id: int) -> bool:
        return super().can_send_in_thread(user_id, thread_id)  # type: ignore[misc]

    def can_manage_thread(self, user_id: int, thread_id: int) -> bool:
        return super().can_manage_thread(user_id, thread_id)  # type: ignore[misc]

    def join_thread(self, user_id: int, thread_id: int) -> ThreadMember:
        return super().join_thread(user_id, thread_id)  # type: ignore[misc]

    def _validate_thread_name(self, name: str) -> str:
        return super()._validate_thread_name(name)  # type: ignore[misc]

    def get_thread(self, user_id: int, thread_id: int) -> Optional[Thread]:
        return super().get_thread(user_id, thread_id)  # type: ignore[misc]

    def _unarchive_thread_internal(self, thread_id: int) -> None:
        super()._unarchive_thread_internal(thread_id)  # type: ignore[misc]
