"""
API critical-path functional tests.

Exercises the core server flows (health contract, registration/login,
messaging, presence, server membership) under repeated and concurrent
load. Timing is not asserted directly: these verify correctness so a
regression surfaces as a deterministic failure instead of a wall-clock
flake.

Run with: pytest -m performance
"""

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from src.core.presence.models import UserStatus

pytestmark = pytest.mark.performance


@pytest.fixture
def test_app(
    auth_manager, messaging_manager, server_manager, presence_manager, rel_manager
):
    """Create FastAPI test application."""
    from src.api.app import create_app
    import src.api as api

    api._auth = auth_manager
    api._messaging = messaging_manager
    api._servers = server_manager
    api._presence = presence_manager
    api._relationships = rel_manager

    app = create_app()
    return app


@pytest.fixture
def api_client(test_app):
    """Create test client."""
    return TestClient(test_app)


class TestAPICriticalPaths:
    """Functional coverage of the API critical paths."""

    def test_health_endpoint_contract(self, api_client):
        response = api_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert isinstance(data.get("version"), str)

    def test_register_login_message_round_trip(self, auth_manager, messaging_manager):
        username = f"api_{uuid.uuid4().hex[:8]}"
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="PerfTest123!",
        )
        partner = auth_manager.register(
            username=f"api2_{uuid.uuid4().hex[:8]}",
            email=f"api2_{uuid.uuid4().hex[:8]}@example.com",
            password="PerfTest123!",
        )
        result = auth_manager.login(username, "PerfTest123!")
        token_info = auth_manager.verify_token(result.token)
        assert token_info.valid
        assert token_info.user_id == user.id

        dm = messaging_manager.create_dm(user.id, partner.id)
        msg = messaging_manager.send_message(
            user_id=user.id, conversation_id=dm.id, content="hello api"
        )
        assert msg is not None

    def test_sustained_message_round_trips(self, messaging_manager, test_dm):
        dm, user1, user2 = test_dm

        for i in range(50):
            msg = messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"sustained {i}"
            )
            assert msg is not None

        messages = messaging_manager.get_messages(
            user_id=user1.id, conversation_id=dm.id, limit=50
        )
        assert len(messages) == 50

    def test_read_heavy_workload(self, messaging_manager, test_dm):
        dm, user1, user2 = test_dm

        for i in range(20):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"read {i}"
            )

        for _ in range(50):
            messages = messaging_manager.get_messages(
                user_id=user1.id, conversation_id=dm.id, limit=10
            )
            assert 0 < len(messages) <= 10

    def test_mixed_workload(
        self, auth_manager, messaging_manager, server_manager, presence_manager
    ):
        username = f"api_{uuid.uuid4().hex[:8]}"
        owner = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="PerfTest123!",
        )
        server = server_manager.create_server(owner.id, "API Mixed Server")
        assert server is not None

        channel = server_manager.get_channels(owner.id, server.id)[0]
        for i in range(20):
            msg = messaging_manager.send_message(
                owner.id, channel.conversation_id, f"mixed {i}"
            )
            assert msg is not None
            presence_manager.set_status(owner.id, UserStatus.ONLINE)

    def test_concurrent_mixed_operations(self, messaging_manager, test_dm):
        dm, user1, user2 = test_dm
        errors = []

        def worker(op_id):
            try:
                if op_id % 2 == 0:
                    messaging_manager.send_message(
                        user_id=user1.id,
                        conversation_id=dm.id,
                        content=f"conc {op_id}",
                    )
                else:
                    messaging_manager.get_messages(user1.id, dm.id, limit=20)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        messages = messaging_manager.get_messages(user1.id, dm.id, limit=50)
        assert len(messages) >= 10
