"""Command-count regression for the recent-messages cache fast path.

A listing served from ``msg:recent:{conversation}`` resolves each id's payload
under ``msg:obj:{id}``.  That used to be one sequential Valkey GET per message
(N round trips for a page of N).  It must be a single MGET instead.
"""

import json

from src.core.database.cache import operations, serialization


def test_recent_cache_page_uses_one_mget_for_payloads(
    messaging_manager, two_users, monkeypatch
):
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    sent = [
        messaging_manager.send_message(user1.id, dm.id, f"msg {i}") for i in range(20)
    ]
    assert len(sent) >= 2

    # Exactly what the cache tier stores (and hands back on a hit).
    payloads = {
        f"msg:obj:{m.id}": json.loads(json.dumps(serialization.ensure_serializable(m)))
        for m in sent
    }

    class _RecentClient:
        """Fake client for the recent-id LIST (lrange/llen only on this path)."""

        def __init__(self, ids):
            self._ids = [str(i) for i in ids]

        def lrange(self, key, start, end):
            return self._ids[start : end + 1]

        def llen(self, key):
            return len(self._ids)

    class _CountingCacheClient:
        """Fake client for payload GETs; counts GET vs MGET commands."""

        def __init__(self):
            self.get_keys = []
            self.mget_calls = 0

        def get_json(self, key):
            self.get_keys.append(key)
            return payloads.get(key)

        def mget_json(self, keys):
            self.mget_calls += 1
            return {k: payloads[k] for k in keys if k in payloads}

    cache = _CountingCacheClient()
    monkeypatch.setattr(operations, "get_client", lambda: cache)
    monkeypatch.setattr(operations, "is_available", lambda: True)
    monkeypatch.setattr("src.core.database.valkey_available", lambda: True)
    monkeypatch.setattr(
        "src.core.database.get_valkey_client",
        lambda: _RecentClient([m.id for m in sent]),
    )

    messages = messaging_manager.get_messages(user1.id, dm.id, 50)

    # Identical results to the sequential path.
    assert [m.id for m in messages] == [m.id for m in sent]
    assert [m.content for m in messages] == [f"msg {i}" for i in range(len(sent))]

    # Command count: N sequential payload GETs -> one batched MGET. Only the
    # payload keys are judged: the listing legitimately reads unrelated cache
    # entries (participation, settings, ...) and one such GET must not be
    # mistaken for a regressed per-message fetch.
    assert cache.mget_calls == 1, (
        f"expected one MGET for {len(sent)} payloads, got {cache.mget_calls}"
    )
    payload_gets = [key for key in cache.get_keys if key.startswith("msg:obj:")]
    assert payload_gets == [], (
        f"expected zero per-message GETs, got {len(payload_gets)}: {payload_gets[:3]}"
    )
