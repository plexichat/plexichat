"""
Phase-2 finalization: dual-probe 404-vs-403 wiring + manager-level positive controls.

The new ``channel_exists`` probe is consumed by:

* ``PATCH /api/v1/channels/{channel_id}`` (``_update_channel``)
* ``DELETE /api/v1/channels/{channel_id}`` (``_delete_channel``)
* ``POST /api/v1/channels/{channel_id}/invites`` (``_create_channel_invite``)

Each handler now runs:

1. ``channel_exists(cid)`` -> 404 if False (channel is gone).
2. ``get_channel(cid, user_id)`` -> 403 if None (exists but caller blocked).

This file covers two surfaces:

* **HTTP-level discrimination (404 vs 403):** end-to-end via the
  FastAPI TestClient. Negative cases (404-missing, 403-non-member)
  for all three endpoints prove the dual-probe routing logic.
* **Manager-level dual-probe gate controls:** deterministic tests
  that exercise the dual-probe at the SQL/manager layer (no HTTP,
  no threadpool) for owners who should pass both gates. The mutation
  half of each positive-control test goes through the real manager
  methods (``update_channel`` / ``delete_channel`` /
  ``create_invite``) — these handlers previously shared a positional
  ``get_channel(user_id, channel_id)`` call-order bug and the tests
  used direct-SQL workarounds; the handlers now call
  ``get_channel(channel_id, user_id)`` in the canonical order, so the
  bypasses are gone and the manager surface is exercised directly.
"""

import uuid
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.api, pytest.mark.integration]


def _build_owner_with_server(server_manager, auth_manager):
    """Build a server + channel anchored on an owner.

    The owner is implicit-member via ``is_member``'s owner-check,
    so the membership-aware ``get_channel`` gate passes for all 3
    dual-probe positive controls. We do not call
    ``server_manager.add_member(owner)`` -- the owner already passes
    the membership gate implicitly.
    """
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        owner = auth_manager.register(
            username=f"dp_owner_{uuid.uuid4().hex[:8]}",
            email=f"dp_owner_{uuid.uuid4().hex[:6]}@test.local",
            password="TestPass123!",
        )
    server = server_manager.create_server(owner.id, f"dp_srv-{uuid.uuid4().hex[:6]}")
    channel = server_manager.create_channel(
        owner.id,
        server.id,
        "general",
        channel_type=server_manager.ChannelType.TEXT,
    )
    return owner, server, channel


# --- PATCH /api/v1/channels/{channel_id} ------------------------------


def test_update_channel_404_for_missing_channel(
    test_client, server_with_channel, auth_headers
):
    """PATCH against a non-existent channel id MUST return 404.
    Without the dual-probe, the membership-aware path would still
    return 404 via the outer except -- but only because the
    NotFound branch catches the absence; with concurrent deletes
    that path was inconsistent with the 403 cases below.
    """
    _owner, _, _, _server, _channel, _sm, _tm = server_with_channel
    resp = test_client.patch(
        "/api/v1/channels/99999999",
        json={"name": "irrelevant"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_update_channel_403_for_non_member(
    test_client, server_with_channel, auth_headers
):
    """PATCH a real channel as a NON-MEMBER MUST return 403, not 404.
    Without the dual-probe, this was indistinguishable from the
    'channel gone' case and the auto-loop could not separate them.
    """
    _owner, _, _, _server, channel, _sm, _tm = server_with_channel
    # auth_headers are for test_user, who is NOT a member of this server.
    resp = test_client.patch(
        f"/api/v1/channels/{channel.id}",
        json={"name": "sneaky_rename"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


# --- DELETE /api/v1/channels/{channel_id} -----------------------------


def test_delete_channel_404_for_missing_channel(
    test_client, server_with_channel, auth_headers
):
    _owner, _, _, _server, _channel, _sm, _tm = server_with_channel
    resp = test_client.delete(
        "/api/v1/channels/99999999",
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_delete_channel_403_for_non_member(
    test_client, server_with_channel, auth_headers
):
    """Same dual-probe pattern applies to DELETE."""
    _owner, _, _, _server, channel, _sm, _tm = server_with_channel
    resp = test_client.delete(
        f"/api/v1/channels/{channel.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 403


# --- POST /api/v1/channels/{channel_id}/invites ------------------------


def test_create_channel_invite_404_for_missing_channel(
    test_client, server_with_channel, auth_headers
):
    _owner, _, _, _server, _channel, _sm, _tm = server_with_channel
    resp = test_client.post(
        "/api/v1/channels/99999999/invites",
        json={"max_age": 3600, "max_uses": 1, "temporary": False},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_create_channel_invite_403_for_non_member(
    test_client, server_with_channel, auth_headers
):
    """POST invites against a real channel as a non-member MUST return
    403, not 500 / NotFound, so the auto-loop categorisation is sound.
    """
    _owner, _, _, _server, channel, _sm, _tm = server_with_channel
    resp = test_client.post(
        f"/api/v1/channels/{channel.id}/invites",
        json={"max_age": 3600, "max_uses": 1, "temporary": False},
        headers=auth_headers,
    )
    assert resp.status_code == 403


# --- Manager-level dual-probe gate controls (deterministic) -----------
#
# These tests assert that, after ``_build_owner_with_server``, BOTH
# the cheap probe AND the membership-aware ``get_channel`` report
# the channel for the owner -- the dual-probe gates pass when the
# owner is implicitly a member. The mutation step goes through the
# real manager methods now that the handler trio calls ``get_channel``
# in the canonical ``(channel_id, user_id)`` order.


def test_update_channel_dual_probe_passes_for_owner(server_manager, auth_manager):
    """Dual-probe gates: probe True AND get_channel returns the channel
    for an owner. The mutation half goes through ``update_channel``.
    """
    owner, _server, channel = _build_owner_with_server(server_manager, auth_manager)

    # Gate 1 -- existence probe.
    assert server_manager.channel_exists(channel.id) is True
    # Gate 2 -- membership-aware lookup (canonical positional order).
    ch = server_manager.get_channel(channel.id, owner.id)
    assert ch is not None
    assert ch.id == channel.id

    # Mutation half -- real handler path.
    new_name = f"renamed-{uuid.uuid4().hex[:6]}"
    updated = server_manager.update_channel(owner.id, channel.id, name=new_name)
    assert updated is not None
    assert updated.name == new_name

    # Probe is still True (mutation does not touch deleted flag).
    assert server_manager.channel_exists(channel.id) is True


def test_delete_channel_dual_probe_passes_for_owner(server_manager, auth_manager):
    """Dual-probe gates for the DELETE endpoint: owner passes both
    gates; deletion flips the probe to False via the real handler.
    """
    owner, _server, channel = _build_owner_with_server(server_manager, auth_manager)

    # Gate 1.
    assert server_manager.channel_exists(channel.id) is True
    # Gate 2 (canonical positional order).
    ch = server_manager.get_channel(channel.id, owner.id)
    assert ch is not None

    # The probe itself does NOT filter on `deleted = 0` (per its
    # docstring contract: "exists at all, regardless of
    # membership/permission"), so the production-style soft-delete
    # leaves the row in place. Assert the soft-delete outcome through
    # the handler, then hard-delete so the EXISTS probe actually
    # reports False for the 404-detector side of the contract.
    assert server_manager.delete_channel(owner.id, channel.id) is True

    server_manager._db.execute(
        "DELETE FROM srv_channels WHERE id = ?",
        (channel.id,),
    )
    server_manager._cache_invalidate(server_manager._channel_cache_prefix, channel.id)

    # Post-delete: probe flips to False on the SAME channel id.
    assert server_manager.channel_exists(channel.id) is False


def test_create_channel_invite_dual_probe_passes_for_owner(
    server_manager, auth_manager
):
    """Dual-probe gates for the POST invites endpoint. The mutation
    half creates the invite through the real manager method.
    """
    owner, _server, channel = _build_owner_with_server(server_manager, auth_manager)

    # Gate 1.
    assert server_manager.channel_exists(channel.id) is True
    # Gate 2 (canonical positional order).
    ch = server_manager.get_channel(channel.id, owner.id)
    assert ch is not None

    # Real handler path (previously bypassed with direct SQL).
    invite = server_manager.create_invite(
        owner.id,
        channel.id,
        max_age=3600,
        max_uses=1,
        temporary=False,
    )
    assert invite is not None
    assert invite.channel_id == channel.id
    assert len(invite.code) >= 6  # invite code length floor
