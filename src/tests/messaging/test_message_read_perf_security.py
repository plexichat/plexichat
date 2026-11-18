"""Regression tests for the two hot message-read endpoints.

Targets:

* ``GET /api/v1/channels/{id}/messages``
* ``GET /api/v1/channels/{id}/messages/unread``

The list path used to degrade to one ``are_embeds_suppressed()`` query per
message whenever the embeds module was wired as a manager instance, and a
channel the caller could not view could be re-read through the DM fallback.
Both paths are pinned here: identical serialized output, bounded fan-out,
and fail-closed permission handling.
"""

import asyncio
import inspect
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.messages import messages_list as ml
from src.api.routes.messages import read_receipts as rr
from src.api.routes.messages.helpers import batch_embed_suppressed_ids
from src.api.routes.messages.render_cache import get_render_cache
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


def _query_counter(db):
    """Wrap the shared db handle and return (counts, restore)."""
    counts = {"n": 0}
    originals = {}

    def wrap(orig):
        def inner(*args, **kwargs):
            counts["n"] += 1
            return orig(*args, **kwargs)

        return inner

    # ``fetch_all``/``fetch_one`` funnel through ``execute``; counting only
    # the latter avoids double-counting the same round trip.
    originals["execute"] = db.execute
    db.execute = wrap(originals["execute"])

    def restore():
        for name, orig in originals.items():
            setattr(db, name, orig)

    return counts, restore


@pytest.mark.api
def test_batched_suppression_matches_per_id_with_both_wirings(
    test_client, db, server_with_channel, messaging_manager
):
    """The batched helper must agree with the legacy per-id path and issue ONE
    query whether ``get_embeds()`` returns a module or a manager.
    """
    owner, _m1, _m2, _server, channel, _sm, _tm = server_with_channel
    msg = messaging_manager.send_message(owner.id, channel.conversation_id, "probe")

    from src.core import embeds as embeds_module

    embed_id = int(msg.id) + 1_000_000
    db.execute(
        "INSERT INTO embed_embeds (id, created_by, created_at) VALUES (?, ?, 1)",
        (embed_id, owner.id),
    )
    db.execute(
        "INSERT INTO embed_message_embeds "
        "(id, message_id, embed_id, position, suppressed, created_at) "
        "VALUES (?, ?, ?, 0, 1, 1)",
        (embed_id + 1, int(msg.id), embed_id),
    )

    ids = [int(msg.id)]
    manager = embeds_module._get_manager()
    for wiring in (embeds_module, manager):
        counts, restore = _query_counter(db)
        try:
            result = batch_embed_suppressed_ids(ids, embeds_mod=wiring)
        finally:
            restore()
        assert result == set(ids)
        assert counts["n"] <= 1, (
            "embed suppression must be resolved in a single batched query "
            f"for {type(wiring).__name__}, got {counts['n']}"
        )
        legacy = {i for i in ids if manager.are_embeds_suppressed(i)}
        assert result == legacy


@pytest.mark.api
def test_message_list_output_identical_batch_vs_per_id(
    test_client, db, server_with_channel, messaging_manager, auth_manager
):
    """Full handler output is byte-identical whether suppression is resolved
    in one batch query or by the old per-message lookups.
    """
    owner, _m1, _m2, _server, channel, _sm, _tm = server_with_channel
    for i in range(20):
        messaging_manager.send_message(owner.id, channel.conversation_id, f"m{i}")
    user = _token_info(owner)

    new_fn = ml.batch_embed_suppressed_ids

    def legacy(ids, embeds_mod=None):
        mod = embeds_mod or __import__("src.api", fromlist=["get_embeds"]).get_embeds()
        return {i for i in ids if mod.are_embeds_suppressed(i)}

    counts, restore = _query_counter(db)
    results = {}
    query_counts = {}

    async def run() -> None:
        for label, fn in (("batched", new_fn), ("legacy", legacy)):
            _clear_caches()
            counts["n"] = 0
            ml.batch_embed_suppressed_ids = fn
            results[label] = await ml.get_channel_messages(
                str(channel.id), 20, None, None, user
            )
            query_counts[label] = counts["n"]

    try:
        asyncio.run(run())
    finally:
        ml.batch_embed_suppressed_ids = new_fn
        restore()

    assert results["batched"] == results["legacy"]
    assert query_counts["batched"] < query_counts["legacy"]


@pytest.mark.unit
def test_page_size_is_bounded_and_snowflakes_validated():
    from src.api.routes.messages.messages_list import get_channel_messages
    from src.api.routes.messages.read_receipts import get_unread_count

    def _constraint(func, name, cls):
        param = inspect.signature(func).parameters[name].default
        return [m for m in getattr(param, "metadata", []) if type(m).__name__ == cls]

    assert any(getattr(m, "le", None) == 100 for m in _constraint(get_channel_messages, "limit", "Le"))
    assert any(getattr(m, "ge", None) == 1 for m in _constraint(get_channel_messages, "limit", "Ge"))

    bad = ("0", "-1", "99000000000000000000", "abc")
    for value in bad:
        for coro, args in (
            (get_channel_messages, (value, 50, None, None, _token_info(SimpleNamespace(id=1, username="u")))),
            (get_unread_count, (value, _token_info(SimpleNamespace(id=1, username="u")))),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(coro(*args))
            assert exc.value.status_code == 400


@pytest.mark.api
def test_denied_channel_cannot_be_read_through_dm_fallback(
    test_client, server_with_channel, messaging_manager
):
    """A participant who lacks ``messages.read`` on a server channel must get
    404, not the linked conversation's messages. Before the fix the
    PermissionDeniedError was swallowed by the server-channel branch and the
    channel id was re-read as a DM conversation id.
    """
    from src.utils import encryption
    from unittest.mock import patch
    from src.core.messaging.models import ParticipantRole

    owner, member, _m2, _server, channel, server_manager, _tm = server_with_channel
    conv_id = channel.conversation_id
    messaging_manager.send_message(owner.id, conv_id, "secret")

    # The member is a participant of the linked conversation, so the old
    # fallback path *would* have returned the messages.
    messaging_manager.add_participant_to_conversations(
        member.id, [conv_id], ParticipantRole.MEMBER
    )
    server_manager.set_channel_override(
        owner.id, channel.id, "member", member.id, deny={"messages.read": True}
    )

    with patch.object(encryption, "verify_password", return_value=True):
        token = server_manager._auth.login(member.username, "TestPass123!").token
    headers = {"Authorization": f"Bearer {token}"}

    resp = test_client.get(f"/api/v1/channels/{channel.id}/messages", headers=headers)
    assert resp.status_code == 404, resp.text


@pytest.mark.api
def test_warm_read_paths_stay_under_budget(
    test_client, server_with_channel, messaging_manager
):
    """Repeated reads of the same page are served from the in-process render
    cache and the unread lookup stays cheap: both well under 20ms/call.
    """
    owner, _m1, _m2, _server, channel, _sm, _tm = server_with_channel
    for i in range(50):
        messaging_manager.send_message(owner.id, channel.conversation_id, f"m{i}")
    user = _token_info(owner)

    async def run():
        samples = {}
        for label, coro, args in (
            ("messages", ml.get_channel_messages, (str(channel.id), 50, None, None, user)),
            ("unread", rr.get_unread_count, (str(channel.id), user)),
        ):
            _clear_caches()
            await coro(*args)  # prime
            t0 = time.perf_counter()
            for _ in range(30):
                await coro(*args)
            samples[label] = (time.perf_counter() - t0) * 1000 / 30
        return samples

    samples = asyncio.run(run())
    assert samples["messages"] < 20, samples
    assert samples["unread"] < 20, samples
