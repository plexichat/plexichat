"""Regression tests for the bounded render cache on the message list path.

BEFORE: each request for the same page re-ran enrichment and serialization
even within the API cache TTL (that cache is a remote Redis/Valkey lookup).

AFTER: repeated pages are served from a bounded, TTL'd in-process LRU, and a
message write bumps the conversation generation that is folded into every
cache key so a stale page can never be served.
"""

import time

import pytest

from src.api.routes.messages.render_cache import MessageRenderCache, get_render_cache
from src.core.messaging import render_generation
from src.core.messaging.repositories.message import MessageRepository


@pytest.mark.unit
def test_render_cache_is_bounded_and_evicts_least_recently_used():
    cache = MessageRenderCache(maxsize=2, ttl=60)

    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1  # 'a' is now most recently used
    cache.put("c", 3)

    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert len(cache) == 2


@pytest.mark.unit
def test_render_cache_entries_expire_on_ttl():
    cache = MessageRenderCache(maxsize=4, ttl=0.01)

    cache.put("page", ["serialized"])
    assert cache.get("page") == ["serialized"]
    time.sleep(0.03)
    assert cache.get("page") is None


@pytest.mark.unit
def test_render_cache_invalidation_and_conversation_removal():
    cache = MessageRenderCache(maxsize=8, ttl=60)
    cache.put(("messages_api", 7, 42, 0), [1])
    cache.put(("messages_api", 9, 43, 0), [2])

    cache.remove_conversation(42)
    assert cache.get(("messages_api", 7, 42, 0)) is None
    assert cache.get(("messages_api", 9, 43, 0)) == [2]

    cache.clear()
    assert len(cache) == 0


@pytest.mark.unit
def test_singleton_cache_exposed_to_routes():
    assert isinstance(get_render_cache(), MessageRenderCache)


@pytest.mark.unit
def test_message_write_bumps_the_conversation_generation(monkeypatch):
    monkeypatch.setattr(
        "src.core.database.invalidate_pattern", lambda *args, **kwargs: 0
    )
    render_generation.clear()
    conversation_id = 987654321
    assert render_generation.get(conversation_id) == 0

    repo = MessageRepository(None)
    repo.invalidate_conversation_cache(conversation_id)
    assert render_generation.get(conversation_id) == 1

    repo.invalidate_conversation_cache(conversation_id)
    assert render_generation.get(conversation_id) == 2
    render_generation.clear()
