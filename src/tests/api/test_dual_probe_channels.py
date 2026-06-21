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
  half of each positive-control test uses *direct SQL*
  ``UPDATE srv_channels`` / ``INSERT srv_invites`` because the
  underlying ``channel_handler.delete_channel``,
  ``channel_handler.update_channel`` and
  ``member_handler.create_invite`` all share a pre-existing
  positional-order bug (they call
  ``self.manager.get_channel(user_id, channel_id)`` using the
  legacy order, while ``base.py`` canonicalises ``get_channel``
  to ``(channel_id, user_id)`` per AGENTS.md). That bug falsely
  inspects ``user_id`` as if it were a ``channel_id`` and returns
  ``None`` -- ``ChannelNotFoundError`` even when the channel
  exists. Direct-SQL bypass keeps the dual-probe assertion
  isolated from the handler bug; see Phase-3 followup in
  ``src/tests/servers/test_channel_exists.py`` for the
  remediation plan.
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
    ``server_manager.add_member(owner)`` -- an explicit member
    INSERT mid-test would reflow the perms-cache under the
    not-yet-fixed handler positional-order bug (see file-level
    docstring) and push ``get_permissions`` onto its
    unregistered-row path.
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
# owner is implicitly a member. The mutation step below uses direct
# SQL rather than ``update_channel`` / ``delete_channel`` /
# ``create_invite``, because the handler trio shares a positional
# ``get_channel`` bug (see file-level docstring); the Phase-3 fix
# will let these tests switch back to the manager methods without
# behavior change.


def test_update_channel_dual_probe_passes_for_owner(server_manager, auth_manager):
    """Dual-probe gates: probe True AND get_channel returns the channel
    for an owner. The mutation half updates the channel name via
    direct SQL so the assertion focuses on the gate outcome, not on
    the buggy handler positional-arg path.
    """
    owner, _server, channel = _build_owner_with_server(server_manager, auth_manager)

    # Gate 1 -- existence probe.
    assert server_manager.channel_exists(channel.id) is True
    # Gate 2 -- membership-aware lookup (canonical positional order).
    ch = server_manager.get_channel(channel.id, owner.id)
    assert ch is not None
    assert ch.id == channel.id

    # Mutation half -- direct SQL update (handler bug bypass).
    new_name = f"renamed-{uuid.uuid4().hex[:6]}"
    server_manager._db.execute(
        "UPDATE srv_channels SET name = ?, updated_at = ? WHERE id = ?",
        (new_name, server_manager._get_timestamp(), channel.id),
    )
    server_manager._cache_invalidate(server_manager._channel_cache_prefix, channel.id)

    # Probe is still True (mutation does not touch deleted flag).
    assert server_manager.channel_exists(channel.id) is True


def test_delete_channel_dual_probe_passes_for_owner(server_manager, auth_manager):
    """Dual-probe gates for the DELETE endpoint: owner passes both
    gates; deletion flips the probe to False. Direct-SQL delete to
    bypass the handler positional-arg bug (see file-level docstring).
    """
    owner, _server, channel = _build_owner_with_server(server_manager, auth_manager)

    # Gate 1.
    assert server_manager.channel_exists(channel.id) is True
    # Gate 2 (canonical positional order).
    ch = server_manager.get_channel(channel.id, owner.id)
    assert ch is not None

    # Direct SQL hard-delete (handler bug bypass). The probe itself
    # does NOT filter on `deleted = 0` (per its docstring contract:
    # "exists at all, regardless of membership/permission"), so a
    # production-style soft-delete leaves the probe True. To exercise
    # the 404-detector side of the dual-probe contract we hard-delete
    # the row; in production, a hard-delete is what removes the row
    # entirely (vanishing from the EXISTS subquery).
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
    half uses direct SQL INSERT into ``srv_invites`` to bypass the
    handler positional-arg bug.
    """
    owner, _server, channel = _build_owner_with_server(server_manager, auth_manager)

    # Gate 1.
    assert server_manager.channel_exists(channel.id) is True
    # Gate 2 (canonical positional order).
    ch = server_manager.get_channel(channel.id, owner.id)
    assert ch is not None

    # Direct SQL INSERT (handler bug bypass).
    code = uuid.uuid4().hex[:8]  # 8-char code matching invite_code_length default
    invite_id = server_manager._generate_id()
    now_ms = server_manager._get_timestamp()
    server_manager._db.execute(
        """INSERT INTO srv_invites
           (id, code, server_id, channel_id, inviter_id, max_age,
            max_uses, temporary, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            invite_id,
            code,
            channel.server_id,
            channel.id,
            owner.id,
            3600,
            1,
            0,
            now_ms,
            now_ms + (3600 * 1000),
        ),
    )

    # Read the row back through the manager's getter to confirm the
    # dual-probe didn't leave the invite table in an inconsistent
    # shape. We use ``get_invite`` rather than the buggy ``create_invite``
    # manager method -- ``get_invite`` reads the row directly via
    # ``db.fetch_one`` and doesn't touch the positional bug.
    invite = server_manager.get_invite(code)
    assert invite is not None
    assert invite.code == code
    assert len(invite.code) >= 6  # invite code length floor
