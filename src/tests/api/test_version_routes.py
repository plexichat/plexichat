"""Tests for version and server-status routes."""

import asyncio

from main import PlexichatServer
from src.api.routes.version import get_server_state, set_server_state
from src.api.schemas.version import ServerState


class TestVersionStatusRoutes:
    """Tests for server status route behavior."""

    def test_status_route_reflects_server_state_updates(self, test_client):
        """Server status should update immediately after internal state changes."""
        set_server_state(ServerState.RUNNING)
        test_client.get("/api/v1/status")

        try:
            set_server_state(
                ServerState.MAINTENANCE,
                message="Scheduled maintenance",
                estimated_downtime=180,
                restart_at="2026-03-08T12:00:00Z",
            )

            response = test_client.get("/api/v1/status")
            assert response.status_code == 200

            data = response.json()
            assert data["state"] == "maintenance"
            assert data["maintenance_message"] == "Scheduled maintenance"
            assert data["estimated_downtime_seconds"] == 180
            assert data["restart_at"] == "2026-03-08T12:00:00Z"
        finally:
            set_server_state(ServerState.RUNNING)

    def test_status_route_clears_metadata_when_server_recovers(self, test_client):
        """Running state should clear maintenance metadata in cached responses."""
        set_server_state(
            ServerState.MAINTENANCE,
            message="Short maintenance",
            estimated_downtime=60,
            restart_at="2026-03-08T12:05:00Z",
        )
        test_client.get("/api/v1/status")

        try:
            set_server_state(ServerState.RUNNING)

            response = test_client.get("/api/v1/status")
            assert response.status_code == 200

            data = response.json()
            assert data["state"] == "running"
            assert data["maintenance_message"] is None
            assert data["estimated_downtime_seconds"] is None
            assert data["restart_at"] is None
        finally:
            set_server_state(ServerState.RUNNING)


class TestServerLifecycleStatusUpdates:
    """Tests for server lifecycle status integration."""

    def test_notify_clients_restart_updates_server_state(self, monkeypatch):
        """Restart notifications should also update the status endpoint state."""
        import src.api.websocket as websocket

        server = PlexichatServer()
        broadcasts = []

        async def fake_broadcast(status_data):
            broadcasts.append(status_data)
            return 1

        monkeypatch.setattr(websocket, "is_setup", lambda: True)
        monkeypatch.setattr(websocket, "broadcast_server_status", fake_broadcast)

        try:
            set_server_state(ServerState.RUNNING)
            asyncio.run(server.notify_clients_restart(25))

            assert get_server_state() == ServerState.RESTARTING
            assert broadcasts == [
                {
                    "state": "restarting",
                    "message": "Server is restarting",
                    "estimated_downtime_seconds": 25,
                }
            ]
        finally:
            set_server_state(ServerState.RUNNING)

