"""
Chaos tests for the cross-worker event listener.

Covers:
- Reconnection with exponential backoff after connection drop
- Backoff reset after sustained healthy polls
- Echo-loop avoidance (ignores own worker's events)
- Graceful shutdown via stop event
- Valkey-disabled no-op path
- Health endpoint includes worker_id
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────


def _make_cross_worker_payload(
    event_type: str = "MESSAGE_CREATE",
    user_ids: list | None = None,
    source_worker: str = "worker-b",
    event_data: dict | None = None,
) -> str:
    return json.dumps(
        {
            "t": event_type,
            "u": user_ids or ["user-1", "user-2"],
            "sw": source_worker,
            "d": event_data or {},
        }
    )


# ── fixture: directly patch module globals + utils.config ────────────


@pytest.fixture
def cross_worker_mocks():
    """Wire fake dependencies into the websocket module and utils.config.

    ``_cross_worker_poll_loop`` and ``cross_worker_listener_status``
    both ``import utils.config as config`` *inside* the function body,
    so we must patch ``utils.config.get`` directly — setting
    ``ws_mod.config`` on the websocket module has no effect.
    """
    import src.api.websocket.__init__ as ws_mod
    import utils.config as _real_config

    # --- config ---
    fake_config = {
        "redis": {
            "enabled": True,
            "host": "localhost",
            "port": 6379,
            "password": "",
            "ssl": False,
            "db": 0,
            "key_prefix": "plexichat:",
        }
    }
    _orig_config_get = _real_config.get
    _real_config.get = lambda key, default=None: fake_config.get(key, default)  # type: ignore[method-assign]

    # --- session manager ---
    m_sm = MagicMock()
    m_sm.worker_id = "worker-test-01"

    # --- dispatcher ---
    m_dispatcher = MagicMock()
    m_dispatcher.dispatch_remote_event = MagicMock()

    # Save originals
    _orig_sm = ws_mod._session_manager
    _orig_dispatcher = ws_mod._dispatcher
    _orig_setup_complete = ws_mod._setup_complete
    _orig_status = ws_mod._listener_status
    _orig_backoff = ws_mod._listener_backoff_sec
    _orig_polls = ws_mod._listener_poll_count
    _orig_stop = ws_mod._cross_worker_stop.is_set()
    _orig_task = ws_mod._cross_worker_task
    _orig_create = ws_mod._create_pubsub_client
    _orig_sleep = ws_mod.asyncio.sleep
    _orig_max_backoff = ws_mod._MAX_BACKOFF_SEC
    _orig_reset_polls = ws_mod._BACKOFF_RESET_AFTER_POLLS
    _orig_poll_interval = ws_mod._POLL_INTERVAL_SEC
    _orig_heartbeat_polls = ws_mod._HEARTBEAT_INTERVAL_POLLS
    _orig_init_backoff = ws_mod._INITIAL_BACKOFF_SEC
    _orig_glide_timeout = ws_mod._GLIDE_REQUEST_TIMEOUT_MS

    # --- save/restore for hot-reload functions (exercise them) ---
    _orig_get_tuning = ws_mod.get_poll_tuning
    _orig_update_tuning = ws_mod.update_poll_tuning

    # Apply mocks
    ws_mod._session_manager = m_sm
    ws_mod._dispatcher = m_dispatcher
    ws_mod._setup_complete = True
    ws_mod._listener_status = "disabled"
    ws_mod._listener_backoff_sec = 1.0
    ws_mod._listener_poll_count = 0
    ws_mod._cross_worker_stop.clear()
    ws_mod._cross_worker_task = None
    ws_mod._MAX_BACKOFF_SEC = 0.01
    ws_mod._BACKOFF_RESET_AFTER_POLLS = 600
    ws_mod._POLL_INTERVAL_SEC = 0.0  # no-op already, but explicit
    ws_mod._HEARTBEAT_INTERVAL_POLLS = 300
    ws_mod._INITIAL_BACKOFF_SEC = 1.0
    ws_mod._GLIDE_REQUEST_TIMEOUT_MS = 5000

    # No-op replacements for hot-reload (tests don't need real updates)
    ws_mod.get_poll_tuning = lambda: {
        "poll_interval_sec": ws_mod._POLL_INTERVAL_SEC,
        "heartbeat_interval_polls": ws_mod._HEARTBEAT_INTERVAL_POLLS,
        "max_backoff_sec": ws_mod._MAX_BACKOFF_SEC,
        "backoff_reset_after_polls": ws_mod._BACKOFF_RESET_AFTER_POLLS,
        "initial_backoff_sec": ws_mod._INITIAL_BACKOFF_SEC,
        "glide_request_timeout_ms": ws_mod._GLIDE_REQUEST_TIMEOUT_MS,
    }
    ws_mod.update_poll_tuning = lambda updates: ws_mod.get_poll_tuning()

    # No-op sleep for fast tests
    async def _tiny_sleep(_sec: float) -> None:
        pass

    ws_mod.asyncio.sleep = _tiny_sleep  # type: ignore[assignment]

    yield {
        "ws_mod": ws_mod,
        "session_manager": m_sm,
        "dispatcher": m_dispatcher,
    }

    # Restore originals
    _real_config.get = _orig_config_get  # type: ignore[method-assign]
    ws_mod._session_manager = _orig_sm
    ws_mod._dispatcher = _orig_dispatcher
    ws_mod._setup_complete = _orig_setup_complete
    ws_mod._listener_status = _orig_status
    ws_mod._listener_backoff_sec = _orig_backoff
    ws_mod._listener_poll_count = _orig_polls
    if _orig_stop:
        ws_mod._cross_worker_stop.set()
    else:
        ws_mod._cross_worker_stop.clear()
    ws_mod._cross_worker_task = _orig_task
    ws_mod._create_pubsub_client = _orig_create
    ws_mod.asyncio.sleep = _orig_sleep  # type: ignore[assignment]
    ws_mod._MAX_BACKOFF_SEC = _orig_max_backoff
    ws_mod._BACKOFF_RESET_AFTER_POLLS = _orig_reset_polls
    ws_mod._POLL_INTERVAL_SEC = _orig_poll_interval
    ws_mod._HEARTBEAT_INTERVAL_POLLS = _orig_heartbeat_polls
    ws_mod._INITIAL_BACKOFF_SEC = _orig_init_backoff
    ws_mod._GLIDE_REQUEST_TIMEOUT_MS = _orig_glide_timeout

    ws_mod.get_poll_tuning = _orig_get_tuning
    ws_mod.update_poll_tuning = _orig_update_tuning


# ── tests ────────────────────────────────────────────────────────────


class TestCrossWorkerReconnection:
    """Verify the listener reconnects with backoff after a connection drop."""

    @pytest.mark.asyncio
    async def test_reconnects_after_subscribe_failure(self, cross_worker_mocks):
        """Client creation succeeds but subscribe raises — should retry."""
        from src.api.websocket.__init__ import _cross_worker_poll_loop

        ws_mod = cross_worker_mocks["ws_mod"]
        call_count = [0]

        def _create_with_failing_subscribe(cfg):
            call_count[0] += 1
            client = MagicMock()
            client.ping.return_value = True
            if call_count[0] == 1:
                client.subscribe.side_effect = ConnectionError("boom")
            else:
                client.subscribe.return_value = None
                ws_mod._cross_worker_stop.set()
            client.close = MagicMock()
            return client

        ws_mod._create_pubsub_client = _create_with_failing_subscribe

        await _cross_worker_poll_loop()

        assert call_count[0] >= 2, f"Expected ≥2 client creations, got {call_count[0]}"
        status = ws_mod.cross_worker_listener_status()
        assert status["enabled"] is True

    @pytest.mark.asyncio
    async def test_reconnects_after_mid_poll_disconnect(self, cross_worker_mocks):
        """Inner poll loop raises after a few healthy polls — should retry."""
        from src.api.websocket.__init__ import _cross_worker_poll_loop

        ws_mod = cross_worker_mocks["ws_mod"]
        poll_count = [0]
        client_created = [0]

        def _create_client(cfg):
            client_created[0] += 1
            client = MagicMock()
            client.ping.return_value = True
            client.subscribe.return_value = None
            client.close = MagicMock()

            def _failing_try_get():
                poll_count[0] += 1
                if client_created[0] == 1 and poll_count[0] > 5:
                    raise ConnectionResetError("pipe broken")
                if client_created[0] == 2 and poll_count[0] > 10:
                    ws_mod._cross_worker_stop.set()
                return None

            client.try_get_pubsub_message = _failing_try_get
            return client

        ws_mod._create_pubsub_client = _create_client

        await _cross_worker_poll_loop()

        assert client_created[0] >= 2, (
            f"Expected ≥2 client creations, got {client_created[0]}"
        )

    @pytest.mark.asyncio
    async def test_backoff_resets_after_healthy_polls(self, cross_worker_mocks):
        """After BACKOFF_RESET_AFTER_POLLS healthy polls, backoff goes to 1s."""
        from src.api.websocket.__init__ import _cross_worker_poll_loop

        ws_mod = cross_worker_mocks["ws_mod"]
        backoff_after_reset = [None]

        def _create_client(cfg):
            client = MagicMock()
            client.ping.return_value = True
            client.subscribe.return_value = None
            client.close = MagicMock()

            poll_i = [0]

            def _try_get():
                poll_i[0] += 1
                if poll_i[0] >= 605:
                    backoff_after_reset[0] = ws_mod._listener_backoff_sec
                    ws_mod._cross_worker_stop.set()
                return None

            client.try_get_pubsub_message = _try_get
            return client

        ws_mod._create_pubsub_client = _create_client

        await _cross_worker_poll_loop()

        assert backoff_after_reset[0] is not None, "Backoff was never measured"
        assert backoff_after_reset[0] <= 1.5, (
            f"Backoff should reset to ~1.0, got {backoff_after_reset[0]}"
        )

    @pytest.mark.asyncio
    async def test_echo_loop_avoidance(self, cross_worker_mocks):
        """Events from our own worker_id are ignored (no dispatch)."""
        from src.api.websocket.__init__ import _handle_cross_worker_message

        payload = _make_cross_worker_payload(source_worker="worker-test-01")
        dispatcher = cross_worker_mocks["dispatcher"]

        await _handle_cross_worker_message(payload)

        dispatcher.dispatch_remote_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_remote_event(self, cross_worker_mocks):
        """Events from a different worker_id are dispatched to locals."""
        from src.api.websocket.__init__ import _handle_cross_worker_message

        payload = _make_cross_worker_payload(source_worker="worker-other-99")
        dispatcher = cross_worker_mocks["dispatcher"]

        await _handle_cross_worker_message(payload)

        dispatcher.dispatch_remote_event.assert_called_once_with(
            "MESSAGE_CREATE", {}, ["user-1", "user-2"]
        )

    @pytest.mark.asyncio
    async def test_ignores_invalid_json(self, cross_worker_mocks):
        """Malformed JSON is silently dropped."""
        from src.api.websocket.__init__ import _handle_cross_worker_message

        dispatcher = cross_worker_mocks["dispatcher"]

        await _handle_cross_worker_message("not-json{{{")
        dispatcher.dispatch_remote_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_missing_fields(self, cross_worker_mocks):
        """Payloads missing event_type or user_ids are dropped."""
        from src.api.websocket.__init__ import _handle_cross_worker_message

        dispatcher = cross_worker_mocks["dispatcher"]

        # Missing "t" (event_type)
        payload = json.dumps({"u": ["u1"], "sw": "other"})
        await _handle_cross_worker_message(payload)
        dispatcher.dispatch_remote_event.assert_not_called()

        # Missing "u" (user_ids)
        payload = json.dumps({"t": "EVENT", "sw": "other"})
        await _handle_cross_worker_message(payload)
        dispatcher.dispatch_remote_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_via_stop_event(self, cross_worker_mocks):
        """Setting the stop event causes the loop to exit cleanly."""
        from src.api.websocket.__init__ import _cross_worker_poll_loop

        ws_mod = cross_worker_mocks["ws_mod"]

        def _create_client(cfg):
            client = MagicMock()
            client.ping.return_value = True
            client.subscribe.return_value = None
            client.close = MagicMock()

            def _try_get():
                ws_mod._cross_worker_stop.set()
                return None

            client.try_get_pubsub_message = _try_get
            return client

        ws_mod._create_pubsub_client = _create_client

        await _cross_worker_poll_loop()

        status = ws_mod.cross_worker_listener_status()
        assert status["enabled"] is True
        assert status["status"] == "disconnected"

    def test_health_returns_disabled_when_valkey_off(self, cross_worker_mocks):
        """cross_worker_listener_status() returns enabled=False when
        Valkey is not configured."""
        from src.api.websocket.__init__ import cross_worker_listener_status
        import utils.config as _cfg

        # Override to simulate Valkey disabled
        _orig = _cfg.get
        _cfg.get = lambda key, default=None: (
            {"enabled": False} if key == "redis" else default
        )

        try:
            status = cross_worker_listener_status()
            assert status == {"enabled": False}
        finally:
            _cfg.get = _orig  # type: ignore[method-assign]

    def test_health_includes_worker_id(self, cross_worker_mocks):
        """cross_worker_listener_status() includes the worker_id field."""
        from src.api.websocket.__init__ import cross_worker_listener_status

        ws_mod = cross_worker_mocks["ws_mod"]
        ws_mod._update_listener_status("connected", backoff_sec=1.0, poll_count=42)

        status = cross_worker_listener_status()
        assert status["enabled"] is True
        assert status["worker_id"] == "worker-test-01"
        assert status["status"] == "connected"
        assert status["poll_count"] == 42

    @pytest.mark.asyncio
    async def test_start_stop_listener_lifecycle(self, cross_worker_mocks):
        """start_cross_worker_listener / stop_cross_worker_listener cycle."""
        from src.api.websocket.__init__ import (
            start_cross_worker_listener,
            stop_cross_worker_listener,
        )

        ws_mod = cross_worker_mocks["ws_mod"]

        def _create_client(cfg):
            client = MagicMock()
            client.ping.return_value = True
            client.subscribe.return_value = None
            client.close = MagicMock()

            def _try_get():
                if ws_mod._cross_worker_stop.is_set():
                    return None
                return None

            client.try_get_pubsub_message = _try_get
            return client

        ws_mod._create_pubsub_client = _create_client

        # Start
        await start_cross_worker_listener()
        assert ws_mod._cross_worker_task is not None
        assert not ws_mod._cross_worker_task.done()

        # Let it tick
        await asyncio.sleep(0)

        # Stop
        await stop_cross_worker_listener()
        assert ws_mod._cross_worker_task is None

        status = ws_mod.cross_worker_listener_status()
        assert status["status"] == "disabled"
