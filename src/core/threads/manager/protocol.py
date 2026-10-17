from typing import Any, Optional, cast

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
        return cast(ThreadProtocol, super())._get_timestamp()

    def _generate_id(self) -> int:
        return cast(ThreadProtocol, super())._generate_id()

    def can_view_thread(self, user_id: int, thread_id: int) -> bool:
        return cast(ThreadProtocol, super()).can_view_thread(user_id, thread_id)

    def can_send_in_thread(self, user_id: int, thread_id: int) -> bool:
        return cast(ThreadProtocol, super()).can_send_in_thread(user_id, thread_id)

    def can_manage_thread(self, user_id: int, thread_id: int) -> bool:
        return cast(ThreadProtocol, super()).can_manage_thread(user_id, thread_id)

    def join_thread(self, user_id: int, thread_id: int) -> ThreadMember:
        return cast(ThreadProtocol, super()).join_thread(user_id, thread_id)

    def _validate_thread_name(self, name: str) -> str:
        return cast(ThreadProtocol, super())._validate_thread_name(name)

    def get_thread(self, user_id: int, thread_id: int) -> Optional[Thread]:
        return cast(ThreadProtocol, super()).get_thread(user_id, thread_id)

    def _unarchive_thread_internal(self, thread_id: int) -> None:
        cast(ThreadProtocol, super())._unarchive_thread_internal(thread_id)
