"""Feature and thread-message route ids must cross the wire as strings.

Bookmarks, forwarding, last-chat and scheduled-message responses (and the
thread message routes) returned integer snowflake ids, so a JS client read
them as ``Number`` above 2^53 and lost the low bits. The routes now run
their payloads through ``stringify_snowflake_ids``, the same contract the
neighbouring routes already serve.
"""

from types import SimpleNamespace

import pytest

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision


@pytest.mark.asyncio
async def test_add_bookmark_stringifies_ids(monkeypatch):
    import src.api.routes.features.bookmarks as bookmarks_module

    async def _no_dispatch(event_type, user_id, bookmark):
        return None

    monkeypatch.setattr(bookmarks_module, "_dispatch_bookmark_event", _no_dispatch)

    class _BookmarkSvc:
        def add_bookmark(self, user_id, message_id, conversation_id, label=None):
            return {
                "id": BIG,
                "user_id": user_id,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "label": label,
                "created_at": 1700000000000,
            }

    class _Messaging:
        _bookmark_svc = _BookmarkSvc()

    monkeypatch.setattr(bookmarks_module.api, "get_messaging", lambda: _Messaging())

    body = bookmarks_module.BookmarkRequest(
        message_id=str(BIG), conversation_id=str(BIG), label="keep"
    )
    result = await bookmarks_module.add_bookmark(
        body, current_user=SimpleNamespace(user_id=BIG)
    )

    bookmark = result["bookmark"]
    assert bookmark["id"] == str(BIG)
    assert isinstance(bookmark["id"], str)
    assert bookmark["user_id"] == str(BIG)
    assert isinstance(bookmark["user_id"], str)
    assert bookmark["message_id"] == str(BIG)
    assert isinstance(bookmark["message_id"], str)
    assert bookmark["conversation_id"] == str(BIG)
    assert isinstance(bookmark["conversation_id"], str)
    # Non-id numerics keep their shape.
    assert bookmark["created_at"] == 1700000000000


@pytest.mark.asyncio
async def test_list_bookmarks_stringifies_ids(monkeypatch):
    import src.api.routes.features.bookmarks as bookmarks_module
    import src.core.messaging.services.bookmarks as bookmarks_svc_module

    class _BookmarkSvc:
        def __init__(self, db, messaging=None):
            pass

        def get_bookmarks(self, user_id, conv_id=None, limit=50):
            return [
                {
                    "id": BIG,
                    "user_id": user_id,
                    "message_id": BIG,
                    "conversation_id": BIG,
                    "label": None,
                    "created_at": 1700000000001,
                }
            ]

    monkeypatch.setattr(bookmarks_svc_module, "BookmarkService", _BookmarkSvc)
    monkeypatch.setattr(bookmarks_module.api, "get_db", lambda: object())
    monkeypatch.setattr(bookmarks_module.api, "get_messaging", lambda: None)

    result = await bookmarks_module.list_bookmarks(
        conversation_id=None,
        limit=50,
        current_user=SimpleNamespace(user_id=BIG),
    )

    row = result["bookmarks"][0]
    assert row["id"] == str(BIG)
    assert isinstance(row["id"], str)
    assert row["message_id"] == str(BIG)
    assert isinstance(row["message_id"], str)
    assert row["conversation_id"] == str(BIG)
    assert isinstance(row["conversation_id"], str)
    assert row["created_at"] == 1700000000001


@pytest.mark.asyncio
async def test_forward_message_stringifies_ids(monkeypatch):
    import src.api.routes.features.forwarding as forwarding_module
    import src.core.messaging.services.forwarding as forwarding_svc_module

    class _ForwardingSvc:
        def __init__(self, db, messaging=None, participant_svc=None):
            pass

        def forward_message(self, user_id, message_id, target_conversation_id):
            return {
                "forward_id": BIG,
                "original_message_id": message_id,
                "original_author_id": BIG,
                "original_conversation_id": BIG,
                "new_message_id": BIG,
                "target_conversation_id": target_conversation_id,
                "forwarded_by": user_id,
                "created_at": 1700000000002,
            }

    monkeypatch.setattr(forwarding_svc_module, "ForwardingService", _ForwardingSvc)
    monkeypatch.setattr(forwarding_module.api, "get_db", lambda: object())
    monkeypatch.setattr(forwarding_module.api, "get_messaging", lambda: None)

    body = forwarding_module.ForwardMessageRequest(
        message_id=str(BIG), target_conversation_id=str(BIG)
    )
    result = await forwarding_module.forward_message(
        body, current_user=SimpleNamespace(user_id=BIG)
    )

    forward = result["forward"]
    assert forward["forward_id"] == str(BIG)
    assert isinstance(forward["forward_id"], str)
    assert forward["original_message_id"] == str(BIG)
    assert isinstance(forward["original_message_id"], str)
    assert forward["new_message_id"] == str(BIG)
    assert isinstance(forward["new_message_id"], str)
    assert forward["target_conversation_id"] == str(BIG)
    assert isinstance(forward["target_conversation_id"], str)
    assert forward["forwarded_by"] == str(BIG)
    assert isinstance(forward["forwarded_by"], str)
    assert forward["created_at"] == 1700000000002


@pytest.mark.asyncio
async def test_scheduled_message_routes_stringify_ids(monkeypatch):
    import src.api.routes.features.scheduled_messages as scheduled_module
    import src.core.messaging.services.scheduled as scheduled_svc_module

    row = {
        "id": BIG,
        "conversation_id": BIG,
        "author_id": BIG,
        "content": "later",
        "scheduled_at": 1700000000003,
        "status": "pending",
        "created_at": 1700000000004,
    }

    class _ScheduledSvc:
        def __init__(self, db, participant_svc=None):
            pass

        def create_scheduled_message(
            self, user_id, conversation_id, content, scheduled_at
        ):
            return dict(row)

        def list_scheduled_messages(self, user_id, conv_id=None, status=None, limit=50):
            return [dict(row)]

    monkeypatch.setattr(scheduled_svc_module, "ScheduledMessageService", _ScheduledSvc)
    monkeypatch.setattr(scheduled_module.api, "get_db", lambda: object())
    monkeypatch.setattr(scheduled_module.api, "get_messaging", lambda: None)

    body = scheduled_module.ScheduledMessageRequest(
        conversation_id=str(BIG), content="later", scheduled_at=1700000000003
    )
    created = await scheduled_module.create_scheduled_message(
        body, current_user=SimpleNamespace(user_id=BIG)
    )
    assert created["scheduled_message"]["id"] == str(BIG)
    assert isinstance(created["scheduled_message"]["id"], str)
    assert created["scheduled_message"]["conversation_id"] == str(BIG)
    assert isinstance(created["scheduled_message"]["conversation_id"], str)
    assert created["scheduled_message"]["author_id"] == str(BIG)
    assert isinstance(created["scheduled_message"]["author_id"], str)
    assert created["scheduled_message"]["scheduled_at"] == 1700000000003

    listed = await scheduled_module.list_scheduled_messages(
        conversation_id=None,
        status=None,
        limit=50,
        current_user=SimpleNamespace(user_id=BIG),
    )
    first = listed["scheduled_messages"][0]
    assert first["id"] == str(BIG)
    assert isinstance(first["id"], str)
    assert first["conversation_id"] == str(BIG)
    assert isinstance(first["conversation_id"], str)


@pytest.mark.asyncio
async def test_last_chat_routes_stringify_ids(monkeypatch):
    import src.api.routes.features.last_chat as last_chat_module
    import src.core.messaging.services.last_chat as last_chat_svc_module

    state = {
        "conversation_id": BIG,
        "last_message_id": BIG,
        "scroll_position": 0,
        "updated_at": 1700000000005,
    }

    class _LastChatSvc:
        def __init__(self, db, participant_svc=None):
            pass

        def save_last_chat(
            self, user_id, conversation_id, last_message_id, scroll_position=0
        ):
            return dict(state)

        def get_last_chat(self, user_id):
            return dict(state)

        def get_recent_chats(self, user_id, limit=10):
            return [dict(state)]

    monkeypatch.setattr(last_chat_svc_module, "LastChatService", _LastChatSvc)
    monkeypatch.setattr(last_chat_module.api, "get_db", lambda: object())
    monkeypatch.setattr(last_chat_module.api, "get_messaging", lambda: None)

    body = last_chat_module.LastChatRequest(
        conversation_id=str(BIG), last_message_id=str(BIG), scroll_position=0
    )
    saved = await last_chat_module.save_last_chat(
        body, current_user=SimpleNamespace(user_id=BIG)
    )
    assert saved["last_chat"]["conversation_id"] == str(BIG)
    assert isinstance(saved["last_chat"]["conversation_id"], str)
    assert saved["last_chat"]["last_message_id"] == str(BIG)
    assert isinstance(saved["last_chat"]["last_message_id"], str)
    assert saved["last_chat"]["updated_at"] == 1700000000005

    current = await last_chat_module.get_last_chat(
        current_user=SimpleNamespace(user_id=BIG)
    )
    assert current["last_chat"]["conversation_id"] == str(BIG)
    assert isinstance(current["last_chat"]["conversation_id"], str)

    recent = await last_chat_module.get_recent_chats(
        limit=10, current_user=SimpleNamespace(user_id=BIG)
    )
    assert recent["recent_chats"][0]["conversation_id"] == str(BIG)
    assert isinstance(recent["recent_chats"][0]["conversation_id"], str)


@pytest.mark.asyncio
async def test_thread_message_routes_stringify_ids(monkeypatch):
    import src.api.routes.threads as threads_module

    class _ThreadsMod:
        def get_messages(self, user_id, tid, limit=50, before_id=None, after_id=None):
            return [
                {
                    "id": BIG,
                    "thread_id": tid,
                    "message_id": BIG,
                    "user_id": BIG,
                    "author_id": BIG,
                    "content": "hello",
                    "created_at": 1700000000006,
                }
            ]

        def send_message(self, user_id, tid, content):
            return {
                "id": BIG,
                "thread_id": tid,
                "user_id": user_id,
                "content": content,
                "created_at": 1700000000007,
            }

    monkeypatch.setattr(threads_module.api, "get_threads", lambda: _ThreadsMod())

    listed = await threads_module.get_thread_messages(
        str(BIG),
        limit=50,
        before_id=None,
        after_id=None,
        current_user=SimpleNamespace(user_id=BIG),
    )
    assert listed[0]["id"] == str(BIG)
    assert isinstance(listed[0]["id"], str)
    assert listed[0]["thread_id"] == str(BIG)
    assert isinstance(listed[0]["thread_id"], str)
    assert listed[0]["message_id"] == str(BIG)
    assert isinstance(listed[0]["message_id"], str)
    assert listed[0]["user_id"] == str(BIG)
    assert isinstance(listed[0]["user_id"], str)
    assert listed[0]["content"] == "hello"
    assert listed[0]["created_at"] == 1700000000006

    sent = await threads_module.send_thread_message(
        str(BIG),
        threads_module.ThreadMessageRequest(content="hello"),
        current_user=SimpleNamespace(user_id=BIG),
    )
    assert sent["id"] == str(BIG)
    assert isinstance(sent["id"], str)
    assert sent["thread_id"] == str(BIG)
    assert isinstance(sent["thread_id"], str)
    assert sent["user_id"] == str(BIG)
    assert isinstance(sent["user_id"], str)
    assert sent["created_at"] == 1700000000007
