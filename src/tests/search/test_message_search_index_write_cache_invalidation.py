"""Search must not serve cached content for deleted or edited messages.

``MessageSearchMixin`` memoizes the *final* (authorized, enriched) result
page for a short TTL. ``remove_from_index``/``index_message`` used to update
the FTS backend without touching that memo, so a search performed before a
message was deleted (or edited) kept returning the old content on identical
requests: a deleted message stayed searchable, and an edited one kept its
previous text, for the whole cache TTL. The memo now fails closed: every
message index write drops the message result caches at the write boundary.

These tests use the real manager and indexer, so they are non-vacuous: a
build without the invalidation returns the deleted/stale message and fails.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.search]


def _channel_context(modules, user_pool):
    owner = user_pool.get_user()
    server = modules.servers.create_server(owner.id, f"S-{uuid.uuid4().hex[:6]}")
    chan = modules.servers.create_channel(
        owner.id, server.id, f"general-{uuid.uuid4().hex[:6]}"
    )
    row = modules._db.fetch_one(
        "SELECT conversation_id FROM srv_channels WHERE id = ?", (chan.id,)
    )
    return owner, chan, row["conversation_id"]


def test_deleted_message_is_not_returned_from_cached_search(modules, user_pool):
    owner, _chan, conv_id = _channel_context(modules, user_pool)
    mid = 992000001
    needle = f"delete-secret-{uuid.uuid4().hex[:8]}"
    modules.search.clear_cache()

    modules.search.index_message(
        mid,
        needle,
        {
            "author_id": owner.id,
            "conversation_id": conv_id,
            "server_id": _chan.server_id,
            "channel_id": _chan.id,
        },
    )

    # First search primes the result cache with the live message.
    first = modules.search.search_messages_page(
        owner.id, needle, conversation_id=conv_id
    ).results
    assert any(r.message_id == mid for r in first)

    # Deletion removes the document from the index (as delete_message does).
    modules.search.remove_from_index(mid)

    # The identical request must not be served the stale, cached hit.
    second = modules.search.search_messages_page(
        owner.id, needle, conversation_id=conv_id
    ).results
    assert all(r.message_id != mid for r in second), (
        "deleted message content leaked through the search result cache"
    )


def test_edited_message_content_is_not_stale_in_cached_search(modules, user_pool):
    owner, _chan, conv_id = _channel_context(modules, user_pool)
    mid = 992000002
    # Distinct tokens so the FTS MATCH for the old text cannot hit the new
    # content (shared words would make the query match either version).
    old = f"zoldtext{uuid.uuid4().hex[:8]}"
    new = f"qnewtext{uuid.uuid4().hex[:8]}"
    modules.search.clear_cache()

    modules.search.index_message(
        mid,
        old,
        {
            "author_id": owner.id,
            "conversation_id": conv_id,
            "server_id": _chan.server_id,
            "channel_id": _chan.id,
        },
    )
    assert any(
        r.message_id == mid
        for r in modules.search.search_messages_page(
            owner.id, old, conversation_id=conv_id
        ).results
    )

    # Edit rewrites the indexed content (as edit_message does).
    modules.search.index_message(
        mid,
        new,
        {
            "author_id": owner.id,
            "conversation_id": conv_id,
            "server_id": _chan.server_id,
            "channel_id": _chan.id,
        },
    )

    stale = modules.search.search_messages_page(
        owner.id, old, conversation_id=conv_id
    ).results
    fresh = modules.search.search_messages_page(
        owner.id, new, conversation_id=conv_id
    ).results
    assert all(r.message_id != mid for r in stale), (
        "edited message's old content leaked through the result cache"
    )
    assert any(r.message_id == mid for r in fresh), (
        "the control query must still find the newly indexed content"
    )


def test_unmodified_message_is_still_searchable(modules, user_pool):
    """Control: invalidation must not disable cached search results."""
    owner, _chan, conv_id = _channel_context(modules, user_pool)
    mid = 992000003
    needle = f"live-secret-{uuid.uuid4().hex[:8]}"
    modules.search.clear_cache()

    modules.search.index_message(
        mid,
        needle,
        {
            "author_id": owner.id,
            "conversation_id": conv_id,
            "server_id": _chan.server_id,
            "channel_id": _chan.id,
        },
    )
    first = modules.search.search_messages_page(
        owner.id, needle, conversation_id=conv_id
    ).results
    second = modules.search.search_messages_page(
        owner.id, needle, conversation_id=conv_id
    ).results
    assert any(r.message_id == mid for r in first)
    assert any(r.message_id == mid for r in second)
