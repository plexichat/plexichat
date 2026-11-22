"""Regression: a reaction removal must not leave a stale pill in a cached
message-list body.

``GET /channels/{id}/messages`` embeds reaction pills (count + per-viewer
``me``) and caches the serialized page in the ``messages_api`` body cache and
the in-process render LRU.  Reaction mutations only invalidated the
``reaction_read_api`` namespace, so a page cached before a removal kept being
served with a pill for a reaction that no longer existed -- including ``me``
flags that did not match the current viewer.  A removal must make the next
message-list read authoritative.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes.messages import messages_list as ml
from src.api.routes.messages.render_cache import get_render_cache
from src.api.routes.reactions import _invalidate_reaction_read_cache
from src.core.database.cache.base import _mem_cache, _mem_cache_lock


def _clear_caches() -> None:
    with _mem_cache_lock:
        _mem_cache.clear()
    get_render_cache().clear()


def _token_info(user) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user.id,
        username=user.username,
        avatar_url=None,
        badges=[],
    )


def _field(obj, name, default=None):
    # Cached bodies reconstruct as plain dicts (MessageResponse/ReactionResponse
    # are not on the reconstructable-type allowlist); fresh bodies are models.
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _pill(page, emoji="\U0001f44d"):
    for message in page:
        for reaction in _field(message, "reactions", []) or []:
            if str(_field(reaction, "emoji", "")) == emoji:
                return reaction
    return None


@pytest.mark.api
def test_removed_reaction_not_served_from_cached_message_list(
    test_client, messaging_manager, three_users
):
    owner, second, _third = three_users
    reactions = api.get_reactions()
    assert reactions is not None

    group = messaging_manager.create_group(owner.id, "rxn-list-coherence", [second.id])
    msg = messaging_manager.send_message(owner.id, group.id, "react here")
    reactions.add_reaction(second.id, msg.id, "\U0001f44d")

    _clear_caches()
    viewer = _token_info(owner)

    first = asyncio.run(ml.get_channel_messages(str(group.id), 50, None, None, viewer))
    assert _pill(first) is not None
    assert _field(_pill(first), "count") == 1
    assert _field(_pill(first), "me") is False  # owner did not react

    # The exact mutation path the HTTP route drives: core removal followed by
    # the HTTP-layer reaction cache invalidation.
    reactions.remove_reaction(second.id, msg.id, "\U0001f44d")
    _invalidate_reaction_read_cache()

    second_page = asyncio.run(
        ml.get_channel_messages(str(group.id), 50, None, None, viewer)
    )
    assert _pill(second_page) is None, (
        "cached message list still serves a reaction pill whose reaction was removed"
    )


@pytest.mark.api
def test_added_reaction_me_flag_not_stale_in_cached_message_list(
    test_client, messaging_manager, three_users
):
    """The mirror case: a viewer who adds a reaction must not keep seeing a
    cached ``me=False`` pill after the add."""
    owner, second, _third = three_users
    reactions = api.get_reactions()
    assert reactions is not None

    group = messaging_manager.create_group(
        owner.id, "rxn-list-me-coherence", [second.id]
    )
    msg = messaging_manager.send_message(owner.id, group.id, "react here")

    _clear_caches()
    viewer = _token_info(owner)
    first = asyncio.run(ml.get_channel_messages(str(group.id), 50, None, None, viewer))
    assert _pill(first) is None

    reactions.add_reaction(owner.id, msg.id, "\U0001f44d")
    _invalidate_reaction_read_cache()

    second_page = asyncio.run(
        ml.get_channel_messages(str(group.id), 50, None, None, viewer)
    )
    pill = _pill(second_page)
    assert pill is not None
    assert _field(pill, "me") is True
    assert _field(pill, "count") == 1
