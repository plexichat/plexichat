"""
Regression tests for voice cluster fixes.

Covers:
- Janus subscriber feed resolution (exact producer peer id, no string guessing)
- Bandwidth estimator SFU peer namespace + stale-entry pruning
- WS voice handlers surfacing failures via close codes (no silent success)
- Atomic voice channel capacity enforcement (no check/insert TOCTOU race)
- ICE bootstrap endpoint returning configured servers without session minting
- ICE candidate handling transitioning state without a tautological gate
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.voice.signaling.models import SignalingState
from src.core.voice.signaling.sfu.base import MediaKind
from src.core.voice.signaling.sfu.janus import JanusAdapter, stable_numeric_id
from src.core.voice.signaling.manager.quality_mixin import BandwidthEstimator
from src.core.voice.signaling.manager.ice_mixin import ICEMixin
from src.core.voice.signaling.exceptions import NotConnectedError
from src.api.websocket.handlers.voice import VoiceHandler
from src.api.websocket.opcodes import GatewayCloseCode


@pytest.fixture
def signaling_setup(db_and_modules):
    """Setup the signaling module with TURN configured for ICE tests."""
    from src.core.voice.signaling import setup, close as signaling_close

    setup(
        voice_module=db_and_modules[5],
        events_module=None,
        sfu_backend="mediasoup",
        stun_urls=["stun:stun.l.google.com:19302"],
        turn_urls=["turn:turn.example.com:3478"],
        turn_secret="test_secret_key_for_turn",
        turn_ttl=3600,
    )
    yield
    asyncio.run(signaling_close())


class TestJanusFeedResolution:
    """H5: subscriber feeds must resolve from the recorded producer peer."""

    def _adapter(self):
        adapter = JanusAdapter(api_url="http://localhost:8088/janus")
        adapter._janus_sessions["room_123"] = 100
        adapter._janus_handles["room_123"] = {"user_5": 200}
        return adapter

    @pytest.mark.asyncio
    async def test_produce_records_exact_peer_id(self):
        adapter = self._adapter()
        with patch.object(adapter, "_send_message", new=AsyncMock()):
            producer = await adapter.produce(
                "room_123",
                "user_5",
                "transport_1",
                MediaKind.AUDIO,
                {"codecs": []},
            )

        assert adapter._producer_peers[producer.id] == "user_5"

    @pytest.mark.asyncio
    async def test_consume_feed_uses_recorded_peer_not_first_segment(self):
        adapter = self._adapter()
        producer_id = "user_5_audio_1710000000000"
        adapter._producer_peers[producer_id] = "user_5"
        adapter._producer_kinds[producer_id] = MediaKind.AUDIO
        send = AsyncMock()
        with (
            patch.object(adapter, "_attach_plugin", new=AsyncMock(return_value=99)),
            patch.object(adapter, "_send_message", new=send),
        ):
            await adapter.consume("room_123", "user_9", "transport_2", producer_id, {})

        join_call = next(
            call
            for call in send.call_args_list
            if call.args[2].get("request") == "join"
        )
        feed = join_call.args[2]["feed"]
        # Exact peer id recorded at publish time — NOT the first underscore
        # segment ("user"), which the old parsing mapped every peer to.
        assert feed == stable_numeric_id("user_5", "peer")
        assert feed != stable_numeric_id("user", "peer")

    @pytest.mark.asyncio
    async def test_consume_legacy_feed_fallback_drops_kind_and_timestamp(self):
        adapter = self._adapter()
        # No recorded mapping (producer created before the map existed):
        # fallback must strip only the trailing _<kind>_<ts> fields.
        producer_id = "user_5_audio_1710000000000"
        adapter._producer_kinds[producer_id] = MediaKind.AUDIO
        send = AsyncMock()
        with (
            patch.object(adapter, "_attach_plugin", new=AsyncMock(return_value=99)),
            patch.object(adapter, "_send_message", new=send),
        ):
            await adapter.consume("room_123", "user_9", "transport_2", producer_id, {})

        join_call = next(
            call
            for call in send.call_args_list
            if call.args[2].get("request") == "join"
        )
        assert join_call.args[2]["feed"] == stable_numeric_id("user_5", "peer")

    @pytest.mark.asyncio
    async def test_producer_peer_mapping_cleaned_on_close(self):
        adapter = self._adapter()
        adapter._producer_peers["user_5_audio_1710000000000"] = "user_5"
        adapter._producer_kinds["user_5_audio_1710000000000"] = MediaKind.AUDIO

        with patch.object(adapter, "_send_message", new=AsyncMock()):
            result = await adapter.close_producer(
                "room_123", "user_5", "user_5_audio_1710000000000"
            )

        assert result is True
        assert "user_5_audio_1710000000000" not in adapter._producer_peers
        assert "user_5_audio_1710000000000" not in adapter._producer_kinds


class TestBandwidthEstimator:
    """H6: SFU peers keyed user_<id>; stale entries pruned."""

    def _estimator(self, sfu):
        data = {}
        estimator = BandwidthEstimator(
            quality_data=data, sfu_adapter=sfu, check_interval=0.01
        )
        return estimator, data

    @pytest.mark.asyncio
    async def test_targets_user_prefixed_sfu_peer(self):
        sfu = MagicMock()
        sfu.set_preferred_layers = AsyncMock()
        estimator, data = self._estimator(sfu)
        data[(123, 42)] = {"packet_loss": 20.0, "jitter": 5.0, "_ts": time.time()}

        # Run exactly one scan loop: first sleep returns, second cancels.
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            await estimator._run()

        # Peer id must be namespaced like the SFU adapter's connection map
        # (voice_<cid>:user_<id>); a bare numeric id misses every lookup.
        sfu.set_preferred_layers.assert_awaited_once_with(
            "voice_123", "user_42", "", 0, 0
        )

    @pytest.mark.asyncio
    async def test_prunes_stale_entries_from_both_maps(self):
        sfu = MagicMock()
        sfu.set_preferred_layers = AsyncMock()
        estimator, data = self._estimator(sfu)
        data[(123, 42)] = {"packet_loss": 20.0, "jitter": 5.0, "_ts": time.time() - 60}
        estimator._degraded[(123, 42)] = True

        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            await estimator._run()

        assert (123, 42) not in data
        assert (123, 42) not in estimator._degraded
        sfu.set_preferred_layers.assert_not_called()


class TestVoiceHandlerErrorSurfacing:
    """M1: handler failures must return a close code, never silent success."""

    def _connection(self):
        return SimpleNamespace(is_authenticated=True, user_id=7)

    @pytest.mark.asyncio
    async def test_voice_connect_failure_returns_close_code(self):
        handler = VoiceHandler()
        with patch("src.core.voice.join_channel", side_effect=RuntimeError("boom")):
            result = await handler.handle_voice_connect(
                self._connection(), {"channel_id": 5}
            )

        assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))

    @pytest.mark.asyncio
    async def test_voice_state_update_failure_returns_close_code(self):
        handler = VoiceHandler()
        with patch(
            "src.core.voice.update_voice_state",
            side_effect=RuntimeError("boom"),
        ):
            result = await handler.handle_voice_state_update(
                self._connection(), {"channel_id": 5}
            )

        assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))

    @pytest.mark.asyncio
    async def test_voice_disconnect_failure_returns_close_code(self):
        handler = VoiceHandler()
        with patch("src.core.voice.leave_channel", side_effect=RuntimeError("boom")):
            result = await handler.handle_voice_disconnect(self._connection(), None)

        assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))


class TestAtomicChannelCapacity:
    """M4: capacity check and insert must be atomic (no overflow race)."""

    def test_join_race_cannot_overflow_limit(self, server_with_voice):
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        voice.set_user_limit(owner.id, voice_channel.id, 2)
        voice.join_channel(owner.id, voice_channel.id)
        voice.join_channel(member1.id, voice_channel.id)

        # Simulate the race window: a concurrent join read the count as 1
        # (below the limit) but the channel is already at capacity. The
        # conditional INSERT must reject the write even though the fast-path
        # count check passed.
        with patch.object(voice, "_get_channel_user_count", return_value=1):
            with pytest.raises(voice.ChannelFullError):
                voice.join_channel(member2.id, voice_channel.id)

        # The losing join must not have inserted a voice_states row.
        assert voice.get_voice_state(member2.id) is None

    def test_unlimited_channel_skips_atomic_gate(self, server_with_voice):
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        # user_limit = 0 → plain INSERT path, no WHERE guard.
        voice.set_user_limit(owner.id, voice_channel.id, 0)
        state = voice.join_channel(member2.id, voice_channel.id)
        assert state is not None


class TestIceServersBootstrap:
    """M5: ICE bootstrap returns configured servers without minting a session."""

    def test_get_ice_servers_returns_configured_turn(self, signaling_setup):
        from src.core.voice import signaling

        servers = signaling.get_ice_servers(7)

        urls = [url for s in servers for url in s.urls]
        assert "stun:stun.l.google.com:19302" in urls
        assert "turn:turn.example.com:3478" in urls
        # TURN entry carries time-limited credentials.
        turn = next(s for s in servers if any("turn:" in u for u in s.urls))
        assert turn.username and turn.credential

    def test_route_returns_configured_servers(self, signaling_setup):
        from src.api.routes.voice import get_ice_servers

        response = asyncio.run(get_ice_servers(current_user=SimpleNamespace(user_id=7)))

        urls = [url for s in response.ice_servers for url in s.urls]
        assert "turn:turn.example.com:3478" in urls

    def test_ice_servers_needs_no_channel_or_session(self, signaling_setup):
        """Bootstrap must work for a user who has never joined a channel."""
        from src.core.voice import signaling

        servers = signaling.get_ice_servers(424242)
        assert len(servers) >= 1


class TestIceCandidateStateTransition:
    """L2: valid candidate flips CONNECTING → CONNECTED (no tautology)."""

    class _FakeIceManager(ICEMixin):
        def __init__(self):
            self._connections = {}
            self._ice_manager = MagicMock()

        def _get_timestamp(self):
            return 1234

    def test_valid_candidate_transitions_to_connected(self):
        manager = self._FakeIceManager()
        connection = SimpleNamespace(
            session_id="s1",
            channel_id=7,
            ice_candidates=[],
            state=SignalingState.CONNECTING,
            last_activity=0,
        )
        manager._connections[1] = connection

        result = manager.handle_ice_candidate(
            1,
            7,
            "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host",
            "audio",
            0,
        )

        assert result is True
        assert connection.state == SignalingState.CONNECTED
        assert len(connection.ice_candidates) == 1

    def test_candidate_for_unconnected_user_raises(self):
        manager = self._FakeIceManager()

        with pytest.raises(NotConnectedError):
            manager.handle_ice_candidate(
                1,
                7,
                "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host",
                "audio",
                0,
            )

    def test_candidate_channel_mismatch_rejected(self):
        manager = self._FakeIceManager()
        connection = SimpleNamespace(
            session_id="s1",
            channel_id=7,
            ice_candidates=[],
            state=SignalingState.CONNECTING,
            last_activity=0,
        )
        manager._connections[1] = connection

        result = manager.handle_ice_candidate(
            1,
            99,
            "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host",
            "audio",
            0,
        )

        assert result is False
        assert connection.state == SignalingState.CONNECTING
