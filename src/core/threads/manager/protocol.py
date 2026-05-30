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

    def can_view_thread(self, user_id: int, thread_id: int) -> bool: ...
    def can_send_in_thread(self, user_id: int, thread_id: int) -> bool: ...
    def can_manage_thread(self, user_id: int, thread_id: int) -> bool: ...

    _is_member: Any = None

    def join_thread(self, user_id: int, thread_id: int) -> ThreadMember: ...
    def _validate_thread_name(self, name: str) -> str: ...

    def get_thread(self, user_id: int, thread_id: int) -> Optional[Thread]: ...
    def _unarchive_thread_internal(self, thread_id: int) -> None: ...

    def _get_timestamp(self) -> int: ...
    def _generate_id(self) -> int: ...
