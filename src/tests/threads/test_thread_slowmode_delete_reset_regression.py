"""Deleting a thread message must not reopen the slowmode window.

Thread slowmode keeps the last-send marker in a short-TTL cache and, on a
cache miss, falls back to ``MAX(thread_messages.created_at)``. Both sources
are destroyed by an ordinary message delete (the ``thread_messages`` link is
removed and the marker expires after five minutes), so a member subject to a
long slowmode interval could delete their message, wait out the marker TTL and
post again long before the real window elapsed.

The durable send time lives in ``msg_messages`` (soft-deleted rows are kept),
so the fallback must read it there. This pins that deleting the thread link
never shortens the interval.
"""

import src.core.threads.slowmode as slowmode_mod
from src.core.threads.slowmode import ThreadSlowmode

THREAD_ID = 555
USER_ID = 777
CONVERSATION_ID = 999
INTERVAL_MS = 600_000  # ten minutes -- longer than the 5-minute marker TTL


class _FakeDB:
    """Dispatching fake: the thread link is gone, the message row is not."""

    def __init__(self, msg_sent_at):
        self.msg_sent_at = msg_sent_at

    def fetch_one(self, sql, params=()):
        s = " ".join(sql.split()).lower()
        if "from thread_threads" in s and "slowmode_interval_ms" in s:
            return {
                "slowmode_interval_ms": INTERVAL_MS,
                "slowmode_updated_by": 1,
                "slowmode_updated_at": 1,
            }
        if "from thread_threads" in s and "conversation_id" in s:
            return {"conversation_id": CONVERSATION_ID}
        if "from thread_messages" in s:
            # The link was deleted along with the message.
            return {"last_msg_at": None}
        if "from msg_messages" in s:
            # Soft-deleted message row survives with its original send time.
            return {"last_msg_at": self.msg_sent_at}
        return None

    def execute(self, *args, **kwargs):
        return None


def _expired_marker(monkeypatch):
    """Simulate the short-TTL last-send marker having expired."""
    monkeypatch.setattr(slowmode_mod, "cache_get", lambda key: None)
    monkeypatch.setattr(slowmode_mod, "cache_set", lambda *a, **k: True)


def test_deleted_message_does_not_reset_slowmode_window(monkeypatch):
    _expired_marker(monkeypatch)
    svc = ThreadSlowmode(_FakeDB(msg_sent_at=svc_now() - 1_000))
    result = svc.check_slowmode(THREAD_ID, USER_ID)
    assert result["allowed"] is False, (
        "a member who deleted their message must still wait out the full "
        "slowmode interval"
    )
    assert result["retry_after_ms"] > 0


def test_first_time_sender_is_not_blocked(monkeypatch):
    _expired_marker(monkeypatch)
    svc = ThreadSlowmode(_FakeDB(msg_sent_at=0))
    result = svc.check_slowmode(THREAD_ID, USER_ID)
    assert result["allowed"] is True


def svc_now():
    return ThreadSlowmode(None)._get_timestamp()
