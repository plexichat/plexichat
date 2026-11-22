"""Regression: a block/unblock must not leave a stale, viewer-filtered reaction
count in the HTTP reaction-list cache.

``GET .../messages/{id}/reactions`` memoizes its serialized pills under a
per-credential key (``reaction_read_api``). The count in each pill is *not*
global -- it is computed with the viewer's two-way block filter, so blocking a
reactor changes that viewer's count from 1 to 0. Reaction writes invalidate this
namespace, but block/unblock did not: a reader who blocked a reactor kept
seeing the reactor's pill (and count) from the warm body until the TTL lapsed.
That is a stale, viewer-specific count leaking past a relationship write.

The test drives the real route, the real core aggregation and the real
relationship manager. It proves non-vacuously that the underlying DB truth is
0 after the block (cold read), so the only reason a warm read can still show 1
is the missing invalidation.
"""

import asyncio

import pytest

import src.api as api
from src.api.routes.reactions import (
    _invalidate_reaction_read_cache,
    get_reactions as route_get_reactions,
)
from src.core.auth.models import AccountType, TokenInfo


def _token(uid: int) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user-{uid}",
        account_type=AccountType.USER,
    )


def _clear_reaction_caches() -> None:
    """Drop the L1/L2 HTTP body cache, the message-list caches and the core L0."""
    _invalidate_reaction_read_cache()
    reactions = api.get_reactions()
    if reactions is not None:
        reactions._reaction_read_cache.clear()


def _pills(page):
    return [(p.emoji, p.count, p.me) for p in page]


@pytest.mark.api
def test_block_change_drops_stale_viewer_reaction_count(
    test_client, messaging_manager, three_users, rel_manager
):
    owner, second, _third = three_users
    reactions = api.get_reactions()
    assert reactions is not None

    group = messaging_manager.create_group(owner.id, "rxn-block-cache", [second.id])
    msg = messaging_manager.send_message(owner.id, group.id, "react here")
    reactions.add_reaction(second.id, msg.id, "\U0001f44d")

    _clear_reaction_caches()
    viewer = _token(owner.id)

    first = asyncio.run(
        route_get_reactions(str(group.id), str(msg.id), current_user=viewer)
    )
    assert _pills(first) == [("\U0001f44d", 1, False)]

    # The blocker's two-way block filter must hide second's reaction from here
    # on. No reaction row changed -- only the viewer's block graph did.
    rel_manager.block_user(owner.id, second.id)

    warm = asyncio.run(
        route_get_reactions(str(group.id), str(msg.id), current_user=viewer)
    )
    assert _pills(warm) == [], (
        "cached reaction body still serves a blocked user's reaction/count after block"
    )

    # Non-vacuous: with caches cleared the authoritative core returns no pills,
    # proving the warm failure above is cache staleness, not a wrong expectation.
    _clear_reaction_caches()
    cold = asyncio.run(
        route_get_reactions(str(group.id), str(msg.id), current_user=viewer)
    )
    assert _pills(cold) == []
