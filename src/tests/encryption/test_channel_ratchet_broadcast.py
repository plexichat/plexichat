"""
Tests for the server-side RATCHET_UPDATE broadcast and
messaging.ratchet_allow_legacy_envelopes config gate.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from types import SimpleNamespace

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.session import SessionManager
from src.utils.encryption.channel_ratchet.notify import (
    notify_ratchet_update,
    notify_ratchet_update_async,
)


class TestRatchetUpdateOpcode:
    def test_ratchet_update_opcode_value(self):
        assert GatewayOpcode.RATCHET_UPDATE == 50
        assert GatewayOpcode.RATCHET_UPDATE not in GatewayOpcode._member_map_
        assert GatewayOpcode.RATCHET_UPDATE not in {
            GatewayOpcode.DISPATCH,
            GatewayOpcode.HEARTBEAT,
            GatewayOpcode.HELLO,
            GatewayOpcode.RECONNECT,
            GatewayOpcode.SERVER_STATUS,
            GatewayOpcode.VOICE_CONNECT,
        }


class TestBroadcastRatchetUpdate:
    @pytest.fixture
    def dispatcher(self):
        session_manager = MagicMock(spec=SessionManager)
        return GatewayDispatcher(session_manager=session_manager)

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_authenticated(self, dispatcher):
        conn1 = MagicMock()
        conn1.is_authenticated = True
        conn1.connection_id = "c1"
        conn1.session_id = "s1"
        conn1.intents = 0
        type(conn1).user_id = PropertyMock(return_value=1)

        conn2 = MagicMock()
        conn2.is_authenticated = True
        conn2.connection_id = "c2"
        conn2.session_id = "s2"
        conn2.intents = 0
        type(conn2).user_id = PropertyMock(return_value=2)

        conn3 = MagicMock()
        conn3.is_authenticated = False

        dispatcher._session_manager.get_all_connections.return_value = [
            conn1,
            conn2,
            conn3,
        ]
        conn1.send_json = AsyncMock(return_value=True)
        conn2.send_json = AsyncMock(return_value=True)

        result = await dispatcher.broadcast_ratchet_update(
            conversation_id=42,
            update_data={"reason": "rotation", "new_interval_id": 99},
        )

        assert result == 2
        conn1.send_json.assert_called_once()
        payload = conn1.send_json.call_args[0][0]
        assert payload["op"] == int(GatewayOpcode.RATCHET_UPDATE)
        assert payload["t"] == "RATCHET_UPDATE"
        assert payload["d"]["conversation_id"] == 42
        assert payload["d"]["reason"] == "rotation"
        assert payload["d"]["new_interval_id"] == 99

    @pytest.mark.asyncio
    async def test_broadcast_empty_when_no_connections(self, dispatcher):
        dispatcher._session_manager.get_all_connections.return_value = []
        result = await dispatcher.broadcast_ratchet_update(
            conversation_id=42,
            update_data={"reason": "split"},
        )
        assert result == 0


class TestNotifyRatchetUpdate:
    @pytest.mark.asyncio
    async def test_notify_ratchet_update_async_success(self):
        with patch(
            "src.api.websocket.broadcast_ratchet_update", new_callable=AsyncMock
        ) as mock_broadcast:
            mock_broadcast.return_value = 3
            result = await notify_ratchet_update_async(
                conversation_id=42,
                update_data={"reason": "rotation"},
            )
            assert result == 3
            mock_broadcast.assert_called_once_with(42, {"reason": "rotation"})

    @pytest.mark.asyncio
    async def test_notify_ratchet_update_async_returns_none_on_import_error(self):
        with patch(
            "src.api.websocket.broadcast_ratchet_update", side_effect=ImportError
        ):
            result = await notify_ratchet_update_async(42, {})
            assert result is None

    @pytest.mark.asyncio
    async def test_notify_ratchet_update_async_returns_none_on_broadcast_error(self):
        with patch(
            "src.api.websocket.broadcast_ratchet_update", new_callable=AsyncMock
        ) as mock_broadcast:
            mock_broadcast.side_effect = RuntimeError("boom")
            result = await notify_ratchet_update_async(42, {})
            assert result is None

    def test_notify_ratchet_update_fire_and_forget_no_loop(self):
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            notify_ratchet_update(42, {"reason": "rotation"})

    def test_notify_ratchet_update_fire_and_forget_with_mock_coro(self):
        mock_loop = MagicMock()
        mock_loop.run_coroutine_threadsafe = MagicMock(return_value=MagicMock())

        mock_coro = MagicMock()
        mock_coro_send = MagicMock()
        future_mock = MagicMock()
        mock_coro.send = mock_coro_send
        mock_loop.run_coroutine_threadsafe.return_value = future_mock

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            with patch(
                "src.api.websocket.broadcast_ratchet_update", return_value=MagicMock()
            ):
                notify_ratchet_update(42, {"reason": "split", "new_interval_id": 7})

        mock_loop.run_coroutine_threadsafe.assert_called_once()
        args, _ = mock_loop.run_coroutine_threadsafe.call_args
        assert len(args) == 2
        call_args = mock_loop.run_coroutine_threadsafe.call_args
        assert call_args[0][1] is not None

    def test_notify_ratchet_update_fire_and_forget_exception(self):
        with patch("asyncio.get_running_loop", return_value=MagicMock()):
            with patch(
                "asyncio.run_coroutine_threadsafe", side_effect=RuntimeError("boom")
            ):
                notify_ratchet_update(42, {"reason": "rotation"})


class TestLegacyEnvelopeConfigGate:
    def test_legacy_envelope_allowed_default_true(self):
        from src.utils.encryption.channel_ratchet import manager as manager_mod

        with patch.object(manager_mod, "config") as mock_config:
            mock_config.get = MagicMock(return_value={})
            assert manager_mod._legacy_envelope_allowed() is True

    def test_legacy_envelope_allowed_false_when_configured(self):
        from src.utils.encryption.channel_ratchet import manager as manager_mod

        with patch.object(manager_mod, "config") as mock_config:
            mock_config.get = MagicMock(
                return_value={"ratchet_allow_legacy_envelopes": False}
            )
            assert manager_mod._legacy_envelope_allowed() is False

    def test_legacy_envelope_allowed_true_when_explicitly_true(self):
        from src.utils.encryption.channel_ratchet import manager as manager_mod

        with patch.object(manager_mod, "config") as mock_config:
            mock_config.get = MagicMock(
                return_value={"ratchet_allow_legacy_envelopes": True}
            )
            assert manager_mod._legacy_envelope_allowed() is True

    def test_legacy_envelope_allowed_true_on_config_exception(self):
        from src.utils.encryption.channel_ratchet import manager as manager_mod

        with patch.object(manager_mod, "config") as mock_config:
            mock_config.get = MagicMock(side_effect=RuntimeError("no config"))
            assert manager_mod._legacy_envelope_allowed() is True

    def test_decrypt_message_rejects_legacy_when_disabled(self):
        from src.utils.encryption import decrypt_message

        with patch("src.utils.encryption._legacy_envelope_allowed", return_value=False):
            with pytest.raises(ValueError, match="legacy v1/v2 envelopes are disabled"):
                decrypt_message("ENC:1:somepayload", conversation_id=1, message_id=1)

    def test_decrypt_message_rejects_legacy_v2_when_disabled(self):
        from src.utils.encryption import decrypt_message

        with patch("src.utils.encryption._legacy_envelope_allowed", return_value=False):
            with pytest.raises(ValueError, match="legacy v1/v2 envelopes are disabled"):
                decrypt_message("ENC:2:somepayload", conversation_id=1, message_id=1)

    def test_decrypt_message_allows_legacy_when_enabled(self):
        from src.utils.encryption import decrypt_message

        with patch("src.utils.encryption._legacy_envelope_allowed", return_value=True):
            with patch("src.utils.encryption._get_message_encryptor") as mock_get_enc:
                mock_enc = MagicMock()
                mock_enc.decrypt_message = MagicMock(return_value="decrypted")
                mock_get_enc.return_value = mock_enc
                result = decrypt_message(
                    "ENC:1:somepayload", conversation_id=1, message_id=1
                )
                assert result == "decrypted"
                mock_enc.decrypt_message.assert_called_once_with("ENC:1:somepayload", 1)
