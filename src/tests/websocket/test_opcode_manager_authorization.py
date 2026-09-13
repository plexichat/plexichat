"""Gateway opcode authorization: non-members must be denied.

``ci-tooling/audit_failopen.py`` (sweep D) classifies every authenticated
websocket opcode handler as ``PUBLIC`` because authorization is *delegated* to
the owning manager rather than performed inline. Those delegations were
verified at the manager layer; these tests pin the other half of the contract
at the gateway boundary: if the delegated authority says no, the opcode must
not act.

Two classes of handler are covered, and a completeness test fails if the audit
starts delegating a new opcode that is not classified here:

``RESOURCE_SCOPED``
    The opcode names a server / workbook / artifact / channel the caller may
    not belong to. A non-member must be denied — no subscription, no relay, no
    fan-out, no media session.
``SELF_SCOPED``
    The opcode acts on the caller's own session. It must never honour a
    caller-supplied foreign user id, so a peer cannot act on another user's
    behalf.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.websocket.handlers.artifacts as ha
import src.api.websocket.handlers.presence as hp
import src.api.websocket.handlers.sum as hs
from src.api.websocket.opcodes import GatewayCloseCode
from src.core.servers.exceptions import (
    PermissionDeniedError,
    ServerAccessDeniedError,
)
from src.core.suite.sum.manager import SumPermissionError

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "audit_failopen.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("audit_failopen_ws", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_failopen_ws"] = module
    spec.loader.exec_module(module)
    return module


# Every handler the audit currently reports as "authorization delegated to
# manager", classified by the denial shape the gateway must enforce.
RESOURCE_SCOPED = {
    "handle_artifact_subscribe": "server/conversation-scoped artifact read",
    "handle_artifact_op": "server/conversation-scoped artifact write",
    "handle_request_guild_members": "server membership",
    "handle_typing_start": "channel visibility",
    "handle_sum_subscribe": "workbook grant / ownership",
    "handle_sum_op": "workbook grant / ownership",
    "handle_voice_connect": "voice.connect permission",
    "handle_voice_sdp_offer": "voice.connect permission",
    "handle_voice_ice_candidate": "bound voice connection",
    "handle_voice_speaking": "authoritative voice channel",
}

SELF_SCOPED = {
    "handle_artifact_unsubscribe": "removes only the caller's subscription",
    "handle_sum_unsubscribe": "removes only the caller's subscription",
    "handle_presence_update": "updates only the caller's presence",
    "handle_typing_stop": "clears only the caller's typing state",
    "handle_voice_state_update": "updates only the caller's voice state",
    "handle_voice_disconnect": "disconnects only the caller",
    "handle_voice_sdp_answer": "applies to the caller's own SFU peer",
    "handle_voice_quality": "records the caller's own quality metrics",
}


def _make_artifact(**kwargs):
    attrs = dict(
        artifact_type=SimpleNamespace(),
        server_id=None,
        conversation_id=None,
        author_id=42,
        rev=0,
        payload={},
    )
    attrs.update(kwargs)
    return SimpleNamespace(**attrs)


def _wire_artifacts(monkeypatch, artifact, *, servers=None, messaging=None):
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)
    monkeypatch.setattr(ha.api, "get_servers", lambda: servers)
    monkeypatch.setattr(ha.api, "get_messaging", lambda: messaging)


# === RESOURCE-SCOPED: a non-member is denied ===============================


@pytest.mark.asyncio
async def test_artifact_subscribe_denies_server_non_member(
    monkeypatch, authenticated_connection
):
    """A server-scoped artifact must not sync to a non-member."""
    servers = MagicMock()
    servers.require_permission.side_effect = PermissionDeniedError("not a member")
    _wire_artifacts(monkeypatch, _make_artifact(server_id=7), servers=servers)

    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)

    handler = ha.ArtifactHandler()
    handler._registry = MagicMock()

    result = await handler.handle_artifact_subscribe(
        authenticated_connection, {"artifact_id": 77}, SimpleNamespace()
    )

    assert result == (None, None, None)
    handler._registry.subscribe.assert_not_called()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_artifact_subscribe_denies_conversation_non_participant(
    monkeypatch, authenticated_connection
):
    """A conversation-scoped artifact must not sync to a non-participant."""
    messaging = MagicMock()
    messaging.is_participant.return_value = False
    _wire_artifacts(monkeypatch, _make_artifact(conversation_id=3), messaging=messaging)

    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)

    handler = ha.ArtifactHandler()
    handler._registry = MagicMock()

    result = await handler.handle_artifact_subscribe(
        authenticated_connection, {"artifact_id": 77}, SimpleNamespace()
    )

    assert result == (None, None, None)
    handler._registry.subscribe.assert_not_called()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_artifact_subscribe_fails_closed_when_servers_unwired(
    monkeypatch, authenticated_connection
):
    """With the servers module unwired a server artifact must not leak."""
    _wire_artifacts(monkeypatch, _make_artifact(server_id=7), servers=None)

    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)

    handler = ha.ArtifactHandler()
    handler._registry = MagicMock()

    await handler.handle_artifact_subscribe(
        authenticated_connection, {"artifact_id": 77}, SimpleNamespace()
    )

    handler._registry.subscribe.assert_not_called()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_artifact_op_drops_non_member_write(
    monkeypatch, authenticated_connection
):
    """A non-member's op must not be persisted, relayed, or acked."""
    servers = MagicMock()
    servers.require_permission.side_effect = PermissionDeniedError("not a member")
    _wire_artifacts(monkeypatch, _make_artifact(server_id=7), servers=servers)

    relay = AsyncMock(return_value=1)
    ack = AsyncMock(return_value=True)
    rejected = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "relay_artifact_op", relay)
    monkeypatch.setattr(ha, "send_artifact_ack", ack)
    monkeypatch.setattr(ha, "send_artifact_op_rejected", rejected)

    result = await ha.ArtifactHandler().handle_artifact_op(
        authenticated_connection,
        {"artifact_id": 77, "op": {"op_type": "edit", "content": "x"}},
        SimpleNamespace(_rate_limit_per_minute=None),
    )

    assert result == (None, None, None)
    relay.assert_not_awaited()
    ack.assert_not_awaited()
    rejected.assert_not_awaited()


@pytest.mark.asyncio
async def test_artifact_subscribe_allows_member(monkeypatch, authenticated_connection):
    """Complement: a member's subscribe does register and sync."""
    servers = MagicMock()
    servers.require_permission.return_value = True
    _wire_artifacts(monkeypatch, _make_artifact(server_id=7), servers=servers)

    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)

    handler = ha.ArtifactHandler()
    handler._registry = MagicMock()

    await handler.handle_artifact_subscribe(
        authenticated_connection, {"artifact_id": 77}, SimpleNamespace()
    )

    handler._registry.subscribe.assert_called_once()
    sync.assert_awaited()


@pytest.mark.asyncio
async def test_guild_members_denied_for_non_member(
    monkeypatch, authenticated_connection
):
    """A non-member gets the generic denial frame and no member list."""
    from src.api.websocket.handlers.guild import GuildHandler

    servers = MagicMock()
    servers.get_server.side_effect = ServerAccessDeniedError("not a member")

    dispatcher = MagicMock()
    dispatcher.dispatch_to_connection = AsyncMock(return_value=True)
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher)

    result = await GuildHandler(servers).handle_request_guild_members(
        authenticated_connection, {"guild_id": 7}
    )

    assert result == (None, None, None)
    # No pagination work and no member data.
    servers.get_members.assert_not_called()
    assert dispatcher.dispatch_to_connection.await_count == 1
    event = dispatcher.dispatch_to_connection.await_args.args[1]
    assert event.data["code"] == "guild_members_denied"
    assert "message" not in event.data


@pytest.mark.asyncio
async def test_guild_members_allowed_for_member(monkeypatch, authenticated_connection):
    """Complement: a member receives a truthful (possibly empty) chunk."""
    from src.api.websocket.handlers.guild import GuildHandler

    servers = MagicMock()
    servers.get_server.return_value = SimpleNamespace(id=7)
    servers.get_members.return_value = []

    dispatcher = MagicMock()
    dispatcher.dispatch_to_connection = AsyncMock(return_value=True)
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher)
    monkeypatch.setattr("src.api.get_auth", lambda: None)
    monkeypatch.setattr("src.api.get_presence", lambda: None)

    await GuildHandler(servers).handle_request_guild_members(
        authenticated_connection, {"guild_id": 7}
    )

    assert dispatcher.dispatch_to_connection.await_count == 1
    event = dispatcher.dispatch_to_connection.await_args.args[1]
    assert event.data.get("chunk_count") == 1


@pytest.mark.asyncio
async def test_typing_start_denied_for_channel_non_member(
    monkeypatch, authenticated_connection
):
    """A non-member's typing must not be recorded or fanned out."""
    servers = MagicMock()
    servers.get_channel.return_value = None
    messaging = MagicMock()
    messaging.get_participants.return_value = []
    monkeypatch.setattr("src.api.get_servers", lambda: servers)
    monkeypatch.setattr("src.api.get_messaging", lambda: messaging)

    presence = MagicMock()
    handler = hp.PresenceHandler(presence)
    handler._dispatch_typing_event = AsyncMock()

    await handler.handle_typing_start(authenticated_connection, {"channel_id": 55})

    presence.start_typing.assert_not_called()
    handler._dispatch_typing_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_sum_subscribe_denies_non_member(monkeypatch, authenticated_connection):
    """A SumPermissionError must not register a subscription or send a sync."""
    manager = MagicMock()
    manager.get_workbook.side_effect = SumPermissionError("no access")
    monkeypatch.setattr(hs, "_get_sum_manager", lambda: manager)

    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(hs, "send_sum_sync", sync)

    handler = hs.SumHandler()
    handler._registry = MagicMock()

    result = await handler.handle_sum_subscribe(
        authenticated_connection, {"workbook_id": 9}, SimpleNamespace()
    )

    assert result == (None, None, None)
    handler._registry.subscribe.assert_not_called()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_sum_op_denies_non_member(monkeypatch, authenticated_connection):
    """A non-member's workbook op must not be applied or relayed."""
    manager = MagicMock()
    manager.apply_operation.side_effect = SumPermissionError("no access")
    monkeypatch.setattr(hs, "_get_sum_manager", lambda: manager)

    relay = AsyncMock(return_value=1)
    monkeypatch.setattr(hs, "relay_sum_op", relay)

    result = await hs.SumHandler().handle_sum_op(
        authenticated_connection,
        {
            "workbook_id": 9,
            "op": {
                "operation_id": "op-1",
                "operation_type": "set_cells",
                "sheet_id": 1,
                "base_revision": 0,
                "cells": [{"row": 0, "col": 0, "value": "x"}],
            },
        },
        SimpleNamespace(_rate_limit_per_minute=None),
    )

    assert result == (None, None, None)
    relay.assert_not_awaited()


@pytest.mark.asyncio
async def test_sum_subscribe_allows_grantee(monkeypatch, authenticated_connection):
    """Complement: a grantee's subscribe registers and syncs."""
    manager = MagicMock()
    manager.get_workbook.return_value = {"current_revision": 3}
    manager.replay_operations.return_value = []
    monkeypatch.setattr(hs, "_get_sum_manager", lambda: manager)

    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(hs, "send_sum_sync", sync)

    handler = hs.SumHandler()
    handler._registry = MagicMock()

    await handler.handle_sum_subscribe(
        authenticated_connection, {"workbook_id": 9}, SimpleNamespace()
    )

    handler._registry.subscribe.assert_called_once()
    sync.assert_awaited()


@pytest.mark.asyncio
async def test_voice_connect_denied_when_permission_refused(
    monkeypatch, authenticated_connection
):
    """A refused voice.join must surface as a failure, not a silent success."""
    import src.core.voice as voice

    def _denied(user_id, channel_id):
        raise PermissionDeniedError("voice.connect denied")

    monkeypatch.setattr(voice, "join_channel", _denied)

    handler = _voice_handler()
    handler._dispatch_voice_state_update = AsyncMock()

    result = await handler.handle_voice_connect(
        authenticated_connection, {"channel_id": 55}
    )

    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))
    handler._dispatch_voice_state_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_sdp_offer_denied_without_connect_permission(
    monkeypatch, authenticated_connection
):
    """Signaling refuses the offer for a user who cannot connect."""
    import src.core.voice.signaling as signaling

    async def _denied(user_id, channel_id, sdp):
        raise PermissionDeniedError("voice.connect denied")

    monkeypatch.setattr(signaling, "handle_sdp_offer_async", _denied)

    result = await _voice_handler().handle_voice_sdp_offer(
        authenticated_connection, {"channel_id": 55, "sdp": "v=0"}
    )

    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))


@pytest.mark.asyncio
async def test_voice_ice_candidate_denied_without_connection(
    monkeypatch, authenticated_connection
):
    """A user with no bound connection cannot inject ICE candidates."""
    import src.core.voice.signaling as signaling

    async def _denied(user_id, channel_id, candidate, sdp_mid, sdp_mline_index):
        raise RuntimeError("not connected to voice")

    monkeypatch.setattr(signaling, "handle_ice_candidate_async", _denied)

    result = await _voice_handler().handle_voice_ice_candidate(
        authenticated_connection,
        {"channel_id": 55, "candidate": "candidate:1"},
    )

    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))


@pytest.mark.asyncio
async def test_voice_speaking_cannot_spoof_another_channel(
    monkeypatch, authenticated_connection
):
    """Speaking in channel B while bound to channel A dispatches nothing."""
    import src.core.voice as voice

    monkeypatch.setattr(
        voice, "get_voice_state", lambda user_id: SimpleNamespace(channel_id=999)
    )

    handler = _voice_handler()
    handler._dispatch_voice_state_update = AsyncMock()

    result = await handler.handle_voice_speaking(
        authenticated_connection, {"channel_id": 123}
    )

    assert result == (None, None, None)
    handler._dispatch_voice_state_update.assert_not_awaited()


# === SELF-SCOPED: a foreign user id in the payload is ignored ==============


@pytest.mark.asyncio
async def test_presence_update_only_touches_the_caller(
    monkeypatch, authenticated_connection
):
    presence = MagicMock()
    handler = hp.PresenceHandler(presence)
    handler._dispatch_presence_to_friends = AsyncMock()

    await handler.handle_presence_update(
        authenticated_connection, {"user_id": 999, "status": "online"}
    )

    assert presence.set_status.call_args.args[0] == authenticated_connection.user_id


@pytest.mark.asyncio
async def test_typing_stop_only_clears_the_caller(
    monkeypatch, authenticated_connection
):
    monkeypatch.setattr(hp, "_get_channel_recipients", lambda u, c: ([], None))

    presence = MagicMock()
    handler = hp.PresenceHandler(presence)

    await handler.handle_typing_stop(
        authenticated_connection, {"user_id": 999, "channel_id": 55}
    )

    assert presence.stop_typing.call_args.args == (
        authenticated_connection.user_id,
        55,
    )


@pytest.mark.asyncio
async def test_voice_state_update_only_touches_the_caller(
    monkeypatch, authenticated_connection
):
    import src.core.voice as voice

    update = MagicMock(return_value=SimpleNamespace(channel_id=55))
    monkeypatch.setattr(voice, "update_voice_state", update)

    handler = _voice_handler()
    handler._dispatch_voice_state_update = AsyncMock()

    await handler.handle_voice_state_update(
        authenticated_connection, {"user_id": 999, "channel_id": 55}
    )

    assert update.call_args.kwargs["user_id"] == authenticated_connection.user_id


@pytest.mark.asyncio
async def test_voice_disconnect_only_disconnects_the_caller(
    monkeypatch, authenticated_connection
):
    import src.core.voice as voice
    import src.core.voice.signaling as signaling

    leave = MagicMock(return_value=SimpleNamespace(channel_id=55))
    monkeypatch.setattr(voice, "leave_channel", leave)
    monkeypatch.setattr(signaling, "disconnect_voice_async", AsyncMock())

    handler = _voice_handler()
    handler._dispatch_voice_state_update = AsyncMock()

    await handler.handle_voice_disconnect(authenticated_connection, {"user_id": 999})

    assert leave.call_args.args[0] == authenticated_connection.user_id


@pytest.mark.asyncio
async def test_voice_sdp_answer_only_uses_the_callers_connection(
    monkeypatch, authenticated_connection
):
    import src.core.voice.signaling as signaling

    foreign_peer = MagicMock()
    foreign_peer.sfu_room_id = "room"
    foreign_peer.sfu_peer_id = "peer"
    mgr = MagicMock()
    # Only the *foreign* user has a signaling connection.
    mgr._connections = {999: foreign_peer}
    monkeypatch.setattr(signaling, "_manager", mgr)

    await _voice_handler().handle_voice_sdp_answer(
        authenticated_connection, {"user_id": 999, "sdp": "v=0"}
    )

    mgr._get_sfu.assert_not_called()


@pytest.mark.asyncio
async def test_voice_quality_records_the_callers_own_metrics(
    monkeypatch, authenticated_connection
):
    import src.core.voice.signaling as signaling

    hint = MagicMock(return_value=True)
    monkeypatch.setattr(signaling, "update_quality_hint", hint)

    await _voice_handler().handle_voice_quality(
        authenticated_connection, {"user_id": 999, "channel_id": 55, "quality": "high"}
    )

    assert hint.call_args.args == (55, authenticated_connection.user_id)


@pytest.mark.asyncio
async def test_artifact_unsubscribe_only_removes_the_callers_subscription(
    authenticated_connection,
):
    handler = ha.ArtifactHandler()
    handler._registry = MagicMock()

    await handler.handle_artifact_unsubscribe(
        authenticated_connection, {"artifact_id": 77}
    )

    assert handler._registry.unsubscribe.call_args.args == (
        authenticated_connection.user_id,
        77,
        authenticated_connection.connection_id,
    )


@pytest.mark.asyncio
async def test_sum_unsubscribe_only_removes_the_callers_subscription(
    authenticated_connection,
):
    handler = hs.SumHandler()
    handler._registry = MagicMock()

    await handler.handle_sum_unsubscribe(authenticated_connection, {"workbook_id": 9})

    assert handler._registry.unsubscribe.call_args.args == (
        authenticated_connection.user_id,
        9,
        authenticated_connection.connection_id,
    )


def _voice_handler():
    from src.api.websocket.handlers.voice import VoiceHandler

    return VoiceHandler(MagicMock())


# === Completeness: every delegated opcode is classified ====================


def _delegated_handler_names() -> set[str]:
    audit = _load_audit()
    return {
        hit.symbol
        for hit in audit.sweep_ws_handlers(REPO_ROOT)
        if "delegated to manager" in hit.detail
    }


def test_every_delegated_opcode_handler_is_classified() -> None:
    """A newly delegated opcode must be classified and given a denial test."""
    classified = set(RESOURCE_SCOPED) | set(SELF_SCOPED)
    assert _delegated_handler_names() == classified


def test_classification_sets_do_not_overlap() -> None:
    assert not (set(RESOURCE_SCOPED) & set(SELF_SCOPED))
