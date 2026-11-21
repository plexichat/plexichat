"""Thread message decryption prefetches ratchet intervals in one query.

BEFORE this change each ``ENC:3:`` message on a thread page triggered its own
``channel_ratchet_intervals`` lookup inside ``decrypt_message`` (N+1). The page
now prefetches the conversation's intervals once and serves every decrypt from
that snapshot, so the lookup count is constant regardless of page size.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.threads.manager import ThreadManager

pytestmark = pytest.mark.unit

_CONVERSATION_ID = 777
_INTERVAL_ID = 42
_PAGE_SIZES = (1, 10, 50)


def _thread_row():
    return {
        "id": 1,
        "channel_id": 10,
        "server_id": 20,
        "owner_id": 30,
        "name": "t",
        "name_encrypted": None,
        "thread_type": "public",
        "state": "active",
        "parent_message_id": None,
        "auto_archive_duration": 1440,
        "message_count": 0,
        "member_count": 1,
        "created_at": 1,
        "archived_at": None,
        "last_message_at": 1,
        "conversation_id": _CONVERSATION_ID,
        "locked": 0,
    }


def _v3_rows(count):
    return [
        {
            "id": 1000 + i,
            "thread_id": 1,
            "message_id": 1000 + i,
            "user_id": 30,
            "author_id": 30,
            "content": f"ENC:3:{_INTERVAL_ID}:AAAA",
            "created_at": 1_700_000_000_000 + i,
        }
        for i in range(count)
    ]


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def fetch_one(self, sql, params=()):
        if "thread_threads" in sql:
            return _thread_row()
        return None

    def fetch_all(self, sql, params=()):
        return list(self.rows)


def _install_fakes(monkeypatch, *, prefetch_returns_all=True):
    """Install fake ratchet manager/decrypt and return call counters.

    ``prefetch_returns_all=False`` models the pre-change code path: the store
    still knows the interval, but no snapshot is filled, so each decrypt has
    to fall through to a per-message store read.
    """
    import src.utils.encryption as encryption

    interval = SimpleNamespace(interval_id=_INTERVAL_ID)
    counters = {"prefetch": 0, "store_get": 0, "decrypt": 0}

    class _Store:
        def get_by_id(self, interval_id):
            counters["store_get"] += 1
            return interval if int(interval_id) == _INTERVAL_ID else None

    class _Manager:
        def __init__(self):
            self._store = _Store()

        def list_intervals(self, conversation_id, limit=50):
            counters["prefetch"] += 1
            assert conversation_id == _CONVERSATION_ID
            return [interval] if prefetch_returns_all else []

        def decrypt(self, conversation_id, message_id, envelope):
            interval_id = int(envelope[len("ENC:3:") :].split(":", 1)[0])
            if self._store.get_by_id(interval_id) is None:
                raise ValueError("missing interval")
            return b"decrypted"

    def fake_get_ratchet_manager(db=None):
        return _Manager()

    def fake_decrypt(
        content, message_id, *, conversation_id=None, ratchet_manager=None
    ):
        counters["decrypt"] += 1
        if isinstance(content, str) and content.startswith("ENC:3:"):
            # Mirror production: the v3 branch resolves its interval through
            # the manager's store once per message it decrypts.
            return ratchet_manager.decrypt(
                conversation_id, message_id, content
            ).decode()
        return content

    monkeypatch.setattr(encryption, "get_ratchet_manager", fake_get_ratchet_manager)
    monkeypatch.setattr(encryption, "decrypt_message", fake_decrypt)
    return counters


def _manager(rows):
    mgr = ThreadManager(_FakeDB(rows))
    servers = Mock()
    servers.has_permission = Mock(return_value=True)
    mgr._servers = servers
    return mgr


@pytest.mark.parametrize("page_size", _PAGE_SIZES)
def test_v3_decrypt_uses_one_prefetch_and_no_per_message_store_reads(
    monkeypatch, page_size
):
    counters = _install_fakes(monkeypatch, prefetch_returns_all=True)
    mgr = _manager(_v3_rows(page_size))

    messages = mgr.get_messages(30, 1, limit=page_size)

    assert len(messages) == page_size
    assert all(m["content"] == "decrypted" for m in messages)
    # One conversation-scoped prefetch for the whole page...
    assert counters["prefetch"] == 1
    # ...and zero per-message interval reads (served from the snapshot).
    assert counters["store_get"] == 0
    assert counters["decrypt"] == page_size


@pytest.mark.parametrize("page_size", _PAGE_SIZES)
def test_unbatched_path_would_issue_one_store_read_per_message(monkeypatch, page_size):
    """Control: with no prefetched snapshot the store is read per message.

    Documents the N+1 removed by the batching above: returning an empty
    snapshot (the pre-change behaviour) leaves every decrypt resolving the
    interval through its own store read.
    """
    counters = _install_fakes(monkeypatch, prefetch_returns_all=False)
    mgr = _manager(_v3_rows(page_size))

    messages = mgr.get_messages(30, 1, limit=page_size)

    assert len(messages) == page_size
    assert counters["prefetch"] == 1
    assert counters["store_get"] == page_size
