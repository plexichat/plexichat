"""Regression: every thread route boundary ID must use ``parse_id``.

Most ``/threads`` handlers used to convert their string path params with a
bare ``int()``. That grammar accepts signs, zero, underscores, Unicode digit
homoglyphs and values past the 64-bit snowflake ceiling, so ``thread_id=1_0``
was acted on as thread 10 and a negative/oversized id reached storage. The
file's ``get_thread_messages`` was already migrated to the ASCII-only, range
bounded ``src.core.security.hardening.parse_id``; this suite ratchets the
remaining handlers to the same boundary invariant.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api.routes.threads as threads_route
from src.api.schemas.threads import ThreadCreateRequest, ThreadUpdateRequest


def _user(user_id=123):
    return SimpleNamespace(user_id=user_id)


def _run(coro):
    return asyncio.run(coro)


def _thread():
    """A harmless thread object for the success paths of the handlers."""
    return SimpleNamespace(
        id=5,
        channel_id=8,
        server_id=9,
        owner_id=123,
        name="t",
        thread_type="public",
        state="open",
        parent_message_id=None,
        auto_archive_duration=60,
        message_count=0,
        member_count=0,
        created_at=1,
        archived_at=None,
        last_message_at=None,
        locked=False,
    )


class _RecordingThreads:
    """Core stub: records every call and returns benign values."""

    def __init__(self):
        self.calls = []
        # _parse_type/_parse_duration coerce through these module hooks.
        self.ThreadType = lambda value: value
        self.AutoArchiveDuration = lambda value: value

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def get_thread(self, user_id, thread_id):
        self._record("get_thread", user_id, thread_id)
        return _thread()

    def update_thread(self, user_id, thread_id, name, duration):
        self._record("update_thread", user_id, thread_id)
        return _thread()

    def delete_thread(self, user_id, thread_id):
        self._record("delete_thread", user_id, thread_id)
        return True

    def join_thread(self, user_id, thread_id):
        self._record("join_thread", user_id, thread_id)
        return SimpleNamespace(thread_id=thread_id, user_id=user_id)

    def leave_thread(self, user_id, thread_id):
        self._record("leave_thread", user_id, thread_id)
        return True

    def get_thread_members(self, user_id, thread_id, **kwargs):
        self._record("get_thread_members", user_id, thread_id)
        return []

    def add_member(self, user_id, thread_id, member_user_id):
        self._record("add_member", user_id, thread_id, member_user_id)
        return SimpleNamespace(thread_id=thread_id, user_id=member_user_id)

    def remove_member(self, user_id, thread_id, member_user_id):
        self._record("remove_member", user_id, thread_id, member_user_id)
        return True

    def archive_thread(self, user_id, thread_id):
        self._record("archive_thread", user_id, thread_id)
        return _thread()

    def unarchive_thread(self, user_id, thread_id):
        self._record("unarchive_thread", user_id, thread_id)
        return _thread()

    def lock_thread(self, user_id, thread_id):
        self._record("lock_thread", user_id, thread_id)
        return _thread()

    def unlock_thread(self, user_id, thread_id):
        self._record("unlock_thread", user_id, thread_id)
        return _thread()

    def send_message(self, user_id, thread_id, content):
        self._record("send_message", user_id, thread_id, content)
        return {"id": thread_id, "content": content}


def _patch(monkeypatch, stub):
    monkeypatch.setattr(threads_route, "_get_threads", lambda: stub)
    # Keep event fan-out out of the unit test; the parse guard is the target.
    for name in ("emit_thread_create", "emit_thread_update", "emit_thread_delete"):
        monkeypatch.setattr(threads_route, name, lambda *a, **kw: None)


def _create_request(parent_message_id):
    """Build a request, then bypass schema validation to test the route guard.

    ``SnowflakeID`` normalises ``"1_0"``/Unicode digits via ``int()`` before the
    handler sees them, so injecting the raw value directly is the only way to
    prove the route itself rejects those spellings.
    """
    req = ThreadCreateRequest(name="t", parent_message_id="7")
    req.__dict__["parent_message_id"] = parent_message_id
    return req


def _invokers(bad):
    """Map a case name to a zero-arg callable returning the handler coroutine."""
    return {
        "get_thread": lambda: threads_route.get_thread(bad, current_user=_user()),
        "update_thread": lambda: threads_route.update_thread(
            bad, ThreadUpdateRequest(name="t"), current_user=_user()
        ),
        "delete_thread": lambda: threads_route.delete_thread(bad, current_user=_user()),
        "join_thread": lambda: threads_route.join_thread(bad, current_user=_user()),
        "leave_thread": lambda: threads_route.leave_thread(bad, current_user=_user()),
        "list_thread_members": lambda: threads_route.list_thread_members(
            bad, current_user=_user()
        ),
        "add_member/thread_id": lambda: threads_route.add_thread_member(
            bad, "8", current_user=_user()
        ),
        "add_member/user_id": lambda: threads_route.add_thread_member(
            "8", bad, current_user=_user()
        ),
        "remove_member/thread_id": lambda: threads_route.remove_thread_member(
            bad, "8", current_user=_user()
        ),
        "remove_member/user_id": lambda: threads_route.remove_thread_member(
            "8", bad, current_user=_user()
        ),
        "archive_thread": lambda: threads_route.archive_thread_ep(
            bad, current_user=_user()
        ),
        "unarchive_thread": lambda: threads_route.unarchive_thread_ep(
            bad, current_user=_user()
        ),
        "lock_thread": lambda: threads_route.lock_thread_ep(bad, current_user=_user()),
        "unlock_thread": lambda: threads_route.unlock_thread_ep(
            bad, current_user=_user()
        ),
        "send_thread_message": lambda: threads_route.send_thread_message(
            bad,
            threads_route.ThreadMessageRequest(content="hi"),
            current_user=_user(),
        ),
        "create_channel_thread/parent_message_id": lambda: (
            threads_route.create_channel_thread(
                "8", _create_request(bad), current_user=_user()
            )
        ),
    }


MALFORMED_IDS = (
    "-1",  # negative
    "0",  # sentinel, not a snowflake
    "99000000000000000000",  # past 2**63-1
    "1_0",  # Python int() grammar maps this to 10
    "٤٢",  # Unicode digit homoglyph, accepted by int()
    "abc",
)

_CASES = tuple(sorted(_invokers("5")))


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("bad", MALFORMED_IDS)
def test_malformed_id_is_400_and_never_reaches_manager(monkeypatch, case, bad):
    stub = _RecordingThreads()
    _patch(monkeypatch, stub)

    with pytest.raises(HTTPException) as exc:
        _run(_invokers(bad)[case]())

    assert exc.value.status_code == 400
    assert stub.calls == [], f"{case} forwarded malformed id {bad!r}"


def test_valid_ids_are_forwarded_as_ints(monkeypatch):
    stub = _RecordingThreads()
    _patch(monkeypatch, stub)

    _run(threads_route.get_thread("5", current_user=_user()))

    assert stub.calls == [("get_thread", (123, 5), {})]


def test_valid_member_ids_are_forwarded_as_ints(monkeypatch):
    stub = _RecordingThreads()
    _patch(monkeypatch, stub)

    _run(threads_route.add_thread_member("5", "8", current_user=_user()))

    assert stub.calls == [("add_member", (123, 5, 8), {})]
