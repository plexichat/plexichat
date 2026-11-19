"""Regression tests for the bounded conversation render-generation registry.

``src.core.messaging.render_generation`` used to be an unbounded module-level
dict: every conversation that was read from or written to left an entry behind
for the lifetime of the process. These tests pin the new hard bounds (entry
and byte caps plus TTL) and, crucially, that eviction can never reintroduce a
generation value that would revive a stale message-list render page.
"""

import pytest

from src.core.messaging import render_generation as rg
from src.core.messaging.repositories.message import MessageRepository


@pytest.fixture(autouse=True)
def _clean_generations():
    rg.clear()
    yield
    rg.clear()


@pytest.mark.unit
def test_generation_registry_is_bounded_by_entry_count(monkeypatch):
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 4)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 0)  # isolate the entry cap

    for conversation_id in range(500):
        rg.bump(conversation_id)

    assert len(rg._generations) <= 4
    assert rg.stats()["entries"] <= 4


@pytest.mark.unit
def test_generation_registry_is_bounded_by_bytes(monkeypatch):
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 10_000)
    monkeypatch.setattr(rg, "_MAX_BYTES", 512)  # deliberately tiny
    monkeypatch.setattr(rg, "_TTL_SECONDS", 0)

    for i in range(200):
        rg.bump("conversation-" + ("x" * 64) + str(i))

    assert rg._total_bytes <= rg._MAX_BYTES
    assert rg.stats()["bytes"] <= rg._MAX_BYTES


@pytest.mark.unit
def test_generation_registry_expires_entries_on_ttl(monkeypatch):
    clock = {"now": 1000.0}

    class _Clock:
        @staticmethod
        def monotonic():
            return clock["now"]

    monkeypatch.setattr(rg, "time", _Clock)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 5.0)
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 10_000)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)

    rg.bump("stale-conversation")
    assert "stale-conversation" in rg._generations

    clock["now"] += 10.0
    rg.bump("fresh-conversation")
    assert "stale-conversation" not in rg._generations
    assert "fresh-conversation" in rg._generations


@pytest.mark.unit
def test_eviction_never_reuses_a_previous_generation_value(monkeypatch):
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 1)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 0)

    first = rg.bump("A")
    second = rg.bump("B")  # forces A out of the size-1 registry
    assert second > first
    assert len(rg._generations) <= 1

    # A missing key binds to the (higher) high-water mark, never to A's old
    # value -- so a page cached before eviction cannot be revived.
    assert rg.get("A") == second
    assert rg.get("A") != first

    # A fresh write to A yields a brand-new, never-before-seen value.
    third = rg.bump("A")
    assert third > second and third not in (first, second)
    assert rg.get("A") == third


@pytest.mark.unit
def test_existing_generation_semantics_are_preserved():
    """The pre-hardening get()/bump() contract must not change."""
    rg.clear()
    assert rg.get(42) == 0
    assert rg.bump(42) == 1
    assert rg.get(42) == 1
    assert rg.bump(42) == 2
    assert rg.get(42) == 2


@pytest.mark.unit
def test_message_write_invalidation_still_changes_render_key(monkeypatch):
    """A write changes the generation the message route folds into its key."""
    from src.api.routes.messages.render_cache import MessageRenderCache

    monkeypatch.setattr(
        "src.core.database.invalidate_pattern", lambda *args, **kwargs: 0
    )
    rg.clear()
    conversation_id = 555_000_111

    before = rg.get(conversation_id)
    cache = MessageRenderCache(maxsize=8, ttl=60)
    cache.put(("messages_api", 7, conversation_id, before), ["stale-page"])

    repo = MessageRepository(None)
    repo.invalidate_conversation_cache(conversation_id)

    after = rg.get(conversation_id)
    assert after > before
    assert cache.get(("messages_api", 7, conversation_id, before)) == ["stale-page"]
    # The live route derives a different key, so the stale page is never hit.
    assert cache.get(("messages_api", 7, conversation_id, after)) is None
