"""M6 regression: thread-message API responses never leak ciphertext.

GET /threads/{id}/messages and POST /threads/{id}/messages must not
include the ``content_encrypted`` storage blob even when the manager
layer still carries it (decryption fallback). Stripping happens at the
route serialization layer, not in manager storage.
"""

import asyncio
from types import SimpleNamespace

import src.api.routes.threads as threads_route


def _user(user_id=123):
    return SimpleNamespace(user_id=user_id)


def _run(coro):
    return asyncio.run(coro)


class _StubThreads:
    def __init__(self, messages=None, sent=None):
        self._messages = messages or []
        self._sent = sent

    def get_messages(self, user_id, thread_id, limit=50, before_id=None, after_id=None):
        return [dict(m) for m in self._messages]

    def send_message(self, user_id, thread_id, content):
        if self._sent is not None:
            return dict(self._sent)
        return {"id": 1, "thread_id": thread_id, "content": content}


def _patch_threads(monkeypatch, stub):
    monkeypatch.setattr(threads_route, "_get_threads", lambda: stub)


def test_strip_helper_removes_ciphertext_but_keeps_plaintext():
    stored = {
        "id": 7,
        "thread_id": 9,
        "content": "hello",
        "content_encrypted": "ENC:v1:ciphertext-blob",
    }
    clean = threads_route._strip_message_ciphertext(stored)
    assert "content_encrypted" not in clean
    assert clean["content"] == "hello"
    # Storage dict must not be mutated.
    assert stored["content_encrypted"] == "ENC:v1:ciphertext-blob"


def test_get_thread_messages_strips_ciphertext(monkeypatch):
    stored = [
        {
            "id": 1,
            "thread_id": 5,
            "user_id": 123,
            "content": "hello",
            "content_encrypted": "ENC:v1:blob-one",
        },
        {
            "id": 2,
            "thread_id": 5,
            "user_id": 123,
            "content": "world",
            "content_encrypted": "ENC:v1:blob-two",
        },
    ]
    _patch_threads(monkeypatch, _StubThreads(messages=stored))
    result = _run(threads_route.get_thread_messages("5", current_user=_user()))
    assert len(result) == 2
    for msg in result:
        assert "content_encrypted" not in msg
    assert [m["content"] for m in result] == ["hello", "world"]


def test_send_thread_message_strips_ciphertext(monkeypatch):
    sent = {
        "id": 3,
        "thread_id": 5,
        "user_id": 123,
        "content": "hi",
        "content_encrypted": "ENC:v1:send-blob",
        "created_at": 123456,
    }
    _patch_threads(monkeypatch, _StubThreads(sent=sent))
    body = threads_route.ThreadMessageRequest(content="hi")
    result = _run(threads_route.send_thread_message("5", body, current_user=_user()))
    assert "content_encrypted" not in result
    assert result["content"] == "hi"


def test_manager_still_returns_projected_payload(server_with_channel):
    """Manager-level get_messages must also stay free of storage columns."""
    owner, member1, member2, server, channel, servers, threads = server_with_channel
    thread = threads.create_thread(
        user_id=owner.id, channel_id=channel.id, name="M6 Route Projection"
    )
    threads.send_message(owner.id, thread.id, "hello")
    msgs = threads.get_messages(owner.id, thread.id)
    assert len(msgs) == 1
    assert "content_encrypted" not in msgs[0]
    assert msgs[0]["content"] == "hello"
