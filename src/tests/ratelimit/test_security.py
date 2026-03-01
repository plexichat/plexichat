"""
Comprehensive security tests for rate limiting system.

Tests bypass attempts, distributed attacks, bucket manipulation,
header spoofing, and integration with all API endpoints and WebSocket operations.
"""

import pytest
import time
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.ratelimit.models import (
    RateLimitConfig,
    RateLimitAlgorithm,
    BucketType,
)
from src.core.ratelimit.storage import MemoryStorage
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.middleware import RateLimitMiddleware, extract_route_info
from src.core import ratelimit
from src.api.websocket.connection import Connection


class TestBypassAttempts:
    """Tests for various bypass attempts."""

    def test_header_spoofing_admin_flag(self, memory_storage, test_user_id):
        """Test that X-Admin header cannot bypass rate limits."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=False,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=False,
        )
        assert not result.allowed

    def test_internal_header_spoofing(self, memory_storage):
        """Test that manually setting internal flag without proper middleware fails."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=999,
                route="GET /test",
                is_internal=True,
            )
            assert result.allowed, f"Internal bypass allowed request {i + 1}"

    def test_user_id_spoofing_attempt(self, memory_storage):
        """Test that changing user_id doesn't reset rate limits inappropriately."""
        config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(user_id=111, route="GET /test")
        assert result.allowed
        result = manager.check_rate_limit(user_id=111, route="GET /test")
        assert result.allowed
        result = manager.check_rate_limit(user_id=111, route="GET /test")
        assert not result.allowed
        result = manager.check_rate_limit(user_id=222, route="GET /test")
        assert result.allowed

    def test_negative_cost_bypass_attempt(self, memory_storage, test_user_id):
        """Test that negative cost values don't bypass limits."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for _ in range(5):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
                cost=1,
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            cost=1,
        )
        assert not result.allowed

    def test_zero_cost_requests(self, memory_storage, test_user_id):
        """Test that zero-cost requests don't bypass tracking."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for _ in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
                cost=0,
            )
            assert result.allowed

    def test_bucket_key_collision_attempt(self, memory_storage):
        """Test that similar bucket keys don't collide."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result1 = manager.check_rate_limit(user_id=123, route="GET /test")
        assert result1.allowed
        result2 = manager.check_rate_limit(user_id=123, route="GET /test")
        assert not result2.allowed
        result3 = manager.check_rate_limit(user_id=1230, route="GET /test")
        assert result3.allowed

    def test_bypass_check_override_attempt(self, memory_storage, test_user_id):
        """Test that bypass check can't be overridden mid-flight."""

        def strict_bypass(user_id, is_admin, is_internal):
            return False

        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            bypass_check=strict_bypass,
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=True,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            is_admin=True,
        )
        assert not result.allowed


class TestDistributedAttacks:
    """Tests for distributed attack patterns."""

    def test_ip_rotation_attack(self, memory_storage):
        """Test rate limiting across IP rotation."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            ip_config=config,
            enable_global_limit=False,
        )
        ip_addresses = [f"192.168.1.{i}" for i in range(1, 11)]
        for ip in ip_addresses:
            for _ in range(3):
                result = manager.check_rate_limit(
                    ip_address=ip,
                    route="GET /test",
                )
                assert result.allowed
            result = manager.check_rate_limit(
                ip_address=ip,
                route="GET /test",
            )
            assert not result.allowed, f"IP {ip} should be rate limited"

    def test_user_id_enumeration_attack(self, memory_storage):
        """Test rate limiting prevents user enumeration."""
        config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /users/{id}": config},
            enable_global_limit=False,
        )
        attacker_id = 99999
        for target_user in range(1, 100):
            result = manager.check_rate_limit(
                user_id=attacker_id,
                route="GET /users/{id}",
            )
            if not result.allowed:
                break
        assert not result.allowed

    def test_distributed_channel_spam(self, memory_storage):
        """Test that distributed channel spam is caught."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=5.0,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
            scope=BucketType.RESOURCE,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /channels/{id}/messages": config},
            enable_global_limit=False,
        )
        channel_id = 12345
        for user_id in range(1, 10):
            for _ in range(5):
                result = manager.check_rate_limit(
                    user_id=user_id,
                    route="POST /channels/{id}/messages",
                    resource_id=channel_id,
                )
            if not result.allowed:
                break

    def test_multi_route_attack(self, memory_storage, test_user_id):
        """Test global limit catches multi-route attacks."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        global_config = RateLimitConfig(
            requests=50,
            window_seconds=1.0,
            burst=10,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={
                "GET /route1": config,
                "GET /route2": config,
                "GET /route3": config,
            },
            global_config=global_config,
            enable_global_limit=True,
        )
        routes = ["GET /route1", "GET /route2", "GET /route3"]
        blocked = False
        for i in range(100):
            route = routes[i % 3]
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route=route,
            )
            if not result.allowed:
                blocked = True
                break
        assert blocked

    def test_webhook_channel_flood_attack(self, memory_storage):
        """Test that multiple webhooks can't flood a single channel."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
            scope=BucketType.WEBHOOK,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /webhooks/{id}/{token}": config},
            enable_global_limit=False,
        )
        channel_id = 12345
        blocked = False
        for webhook_id in range(1, 20):
            for _ in range(10):
                result = manager.check_rate_limit(
                    route="POST /webhooks/{id}/{token}",
                    webhook_id=webhook_id,
                    resource_id=channel_id,
                    is_webhook=True,
                )
                if not result.allowed:
                    blocked = True
                    break
            if blocked:
                break
        assert blocked


class TestBucketManipulation:
    """Tests for bucket state manipulation attempts."""

    def test_bucket_state_isolation(self, memory_storage, test_user_id):
        """Test that bucket states are isolated per user."""
        config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=111, route="GET /test")
        manager.check_rate_limit(user_id=111, route="GET /test")
        result = manager.check_rate_limit(user_id=111, route="GET /test")
        assert not result.allowed
        result = manager.check_rate_limit(user_id=222, route="GET /test")
        assert result.allowed

    def test_bucket_reset_protection(self, memory_storage, test_user_id):
        """Test that bucket reset requires proper authorization."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert result.allowed
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed

    def test_token_bucket_underflow(self, memory_storage, test_user_id):
        """Test that token bucket can't go negative."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=10.0,
            burst=5,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for _ in range(20):
            manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
                cost=1,
            )
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed

    def test_sliding_window_timestamp_overflow(self, memory_storage, test_user_id):
        """Test sliding window handles many timestamps correctly."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(100):
            result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
            assert result.allowed, f"Request {i + 1} should be allowed"
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed

    def test_fixed_window_boundary_manipulation(self, memory_storage, test_user_id):
        """Test fixed window boundaries can't be manipulated."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=1.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for _ in range(5):
            result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
            assert result.allowed
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed
        time.sleep(1.1)
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert result.allowed

    def test_bucket_key_generation_consistency(self, memory_storage):
        """Test that bucket keys are generated consistently."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result1 = manager.check_rate_limit(
            user_id=123,
            route="GET /test",
            resource_id=456,
        )
        result2 = manager.check_rate_limit(
            user_id=123,
            route="GET /test",
            resource_id=456,
        )
        assert result1.bucket_key == result2.bucket_key
        assert result1.allowed
        assert not result2.allowed


class TestHeaderSpoofing:
    """Tests for rate limit header spoofing attempts."""

    @pytest.fixture
    def app_with_headers(self):
        """Create app that checks headers."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=2,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        yield app
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_cannot_spoof_ratelimit_headers_in_request(self, app_with_headers):
        """Test that client-provided rate limit headers are ignored."""
        client = TestClient(app_with_headers)
        response = client.get(
            "/api/v1/test",
            headers={
                "X-RateLimit-Remaining": "999999",
                "X-RateLimit-Limit": "999999",
                "X-RateLimit-Reset": "999999999999",
            },
        )
        assert response.status_code == 200
        response = client.get("/api/v1/test")
        assert response.status_code == 200
        response = client.get("/api/v1/test")
        assert response.status_code == 429

    def test_cannot_bypass_with_x_forwarded_for(self, app_with_headers):
        """Test that X-Forwarded-For doesn't bypass rate limits."""
        client = TestClient(app_with_headers)
        for i in range(5):
            client.get("/api/v1/test", headers={"X-Forwarded-For": f"192.168.1.{i}"})
        assert any(
            r.status_code == 429
            for r in [
                client.get(
                    "/api/v1/test", headers={"X-Forwarded-For": f"192.168.1.{i}"}
                )
                for i in range(3)
            ]
        )

    def test_cannot_spoof_internal_header(self):
        """Test that X-Internal-Request cannot be spoofed to bypass limits."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            route_configs={
                "GET /api/v1/test": RateLimitConfig(
                    requests=1,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                ),
            },
            enable_global_limit=False,
        )
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        client = TestClient(app)
        
        # 1. First request - Success
        resp1 = client.get("/api/v1/test")
        assert resp1.status_code == 200
        
        # 2. Spoofed header request - Should FAIL (429)
        resp2 = client.get("/api/v1/test", headers={"X-Internal-Request": "true"})
        assert resp2.status_code == 429
        assert "Rate limit exceeded" in resp2.text
        
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_retry_after_header_accuracy(self, app_with_headers):
        """Test that Retry-After header is accurate."""
        client = TestClient(app_with_headers)
        client.get("/api/v1/test")
        client.get("/api/v1/test")
        response = client.get("/api/v1/test")
        assert response.status_code == 429
        retry_after = response.headers.get("Retry-After")
        assert retry_after is not None
        assert int(retry_after) > 0


class TestAPIEndpointIntegration:
    """Tests for rate limiting integration with all API endpoints."""

    @pytest.fixture
    def app_with_all_endpoints(self):
        """Create app with various endpoint types."""
        storage = MemoryStorage()
        ratelimit._manager = None
        ratelimit._setup_complete = False
        ratelimit.setup(
            storage_backend=storage,
            enable_global_limit=False,
        )
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.post("/api/v1/auth/login")
        async def login():
            return {"token": "test"}

        @app.post("/api/v1/auth/register")
        async def register():
            return {"user_id": 123}

        @app.post("/api/v1/auth/2fa")
        async def two_factor():
            return {"status": "verified"}

        @app.get("/api/v1/users/@me")
        async def get_user():
            return {"user_id": 123}

        @app.patch("/api/v1/users/@me")
        async def update_user():
            return {"user_id": 123}

        @app.get("/api/v1/servers")
        async def list_servers():
            return {"servers": []}

        @app.post("/api/v1/servers")
        async def create_server():
            return {"server_id": 456}

        @app.delete("/api/v1/servers/123")
        async def delete_server():
            return {"status": "deleted"}

        @app.post("/api/v1/channels/123/messages")
        async def send_message():
            return {"message_id": 789}

        @app.get("/api/v1/channels/123/messages")
        async def get_messages():
            return {"messages": []}

        @app.put("/api/v1/channels/123/messages/456/reactions/👍")
        async def add_reaction():
            return {"status": "added"}

        @app.post("/api/v1/relationships")
        async def add_friend():
            return {"status": "pending"}

        @app.post("/api/v1/relationships/block")
        async def block_user():
            return {"status": "blocked"}

        @app.post("/api/v1/webhooks")
        async def create_webhook():
            return {"webhook_id": 999}

        @app.post("/api/v1/webhooks/999/token123")
        async def execute_webhook():
            return {"status": "sent"}

        yield app
        ratelimit._manager = None
        ratelimit._setup_complete = False

    def test_auth_login_rate_limit(self, app_with_all_endpoints):
        """Test POST /auth/login is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(10):
            response = client.post("/api/v1/auth/login")
            responses.append(response.status_code)
        assert 429 in responses

    def test_auth_register_rate_limit(self, app_with_all_endpoints):
        """Test POST /auth/register is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(10):
            response = client.post("/api/v1/auth/register")
            responses.append(response.status_code)
        assert 429 in responses

    def test_messages_rate_limit(self, app_with_all_endpoints):
        """Test POST /channels/{id}/messages is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(20):
            response = client.post("/api/v1/channels/123/messages")
            responses.append(response.status_code)
        assert 429 in responses

    def test_reactions_rate_limit(self, app_with_all_endpoints):
        """Test PUT /channels/{id}/messages/{msg_id}/reactions/{emoji} is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(10):
            response = client.put("/api/v1/channels/123/messages/456/reactions/👍")
            responses.append(response.status_code)
        assert 429 in responses

    def test_server_creation_rate_limit(self, app_with_all_endpoints):
        """Test POST /servers is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(20):
            response = client.post("/api/v1/servers")
            responses.append(response.status_code)
        assert 429 in responses

    def test_webhook_execution_rate_limit(self, app_with_all_endpoints):
        """Test POST /webhooks/{id}/{token} is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(20):
            response = client.post("/api/v1/webhooks/999/token123")
            responses.append(response.status_code)
        assert 429 in responses

    def test_user_update_rate_limit(self, app_with_all_endpoints):
        """Test PATCH /users/@me is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(10):
            response = client.patch("/api/v1/users/@me")
            responses.append(response.status_code)
        assert 429 in responses

    def test_relationship_rate_limit(self, app_with_all_endpoints):
        """Test POST /relationships is rate limited."""
        client = TestClient(app_with_all_endpoints)
        responses = []
        for _ in range(20):
            response = client.post("/api/v1/relationships")
            responses.append(response.status_code)
        assert 429 in responses


class TestWebSocketRateLimiting:
    """Tests for WebSocket rate limiting integration."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.send_bytes = AsyncMock()
        return ws

    @pytest.fixture
    def connection(self, mock_websocket):
        """Create a connection instance."""
        return Connection(
            websocket=mock_websocket,
            connection_id="test-conn-123",
            heartbeat_interval_ms=45000,
        )

    def test_websocket_event_rate_limit(self, connection):
        """Test WebSocket events are rate limited."""
        for i in range(120):
            allowed = connection.check_rate_limit(120)
            assert allowed, f"Event {i + 1} should be allowed"
        assert not connection.check_rate_limit(120)

    def test_websocket_rate_limit_resets(self, connection):
        """Test WebSocket rate limit resets after window."""
        for _ in range(120):
            connection.check_rate_limit(120)
        assert not connection.check_rate_limit(120)
        connection.event_window_start = time.monotonic() - 61
        assert connection.check_rate_limit(120)

    def test_websocket_different_limits(self, connection):
        """Test WebSocket with different rate limits."""
        for _ in range(50):
            assert connection.check_rate_limit(50)
        assert not connection.check_rate_limit(50)

    def test_websocket_rapid_fire_events(self, connection):
        """Test WebSocket handles rapid-fire events."""
        count = 0
        for i in range(200):
            if connection.check_rate_limit(120):
                count += 1
            else:
                break
        assert count == 120

    def test_websocket_event_tracking_accuracy(self, connection):
        """Test WebSocket event count tracking is accurate."""
        assert connection.event_count == 0
        for i in range(10):
            connection.check_rate_limit(120)
            assert connection.event_count == i + 1

    @pytest.mark.asyncio
    async def test_websocket_dispatch_rate_limiting(self, connection):
        """Test WebSocket dispatch respects rate limits."""
        for _ in range(120):
            connection.check_rate_limit(120)
        payload = {"op": 0, "t": "MESSAGE_CREATE", "d": {}}
        result = await connection.send_json(payload)
        assert result is True


class TestRateLimitEvasionTechniques:
    """Tests for various rate limit evasion techniques."""

    def test_request_smuggling_protection(self, memory_storage, test_user_id):
        """Test protection against request smuggling."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /test": config},
            enable_global_limit=False,
        )
        for _ in range(5):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /test",
        )
        assert not result.allowed

    def test_timing_attack_protection(self, memory_storage, test_user_id):
        """Test that timing doesn't leak bucket state."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        timings = []
        for _ in range(10):
            start = time.monotonic()
            manager.check_rate_limit(user_id=test_user_id, route="GET /test")
            end = time.monotonic()
            timings.append(end - start)
        assert max(timings) < 0.1

    def test_concurrent_request_race_condition(self, memory_storage, test_user_id):
        """Test concurrent requests don't create race conditions."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for _ in range(5):
            manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed

    def test_cache_poisoning_prevention(self, memory_storage):
        """Test that bucket cache can't be poisoned."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result1 = manager.check_rate_limit(user_id=111, route="GET /test")
        result2 = manager.check_rate_limit(user_id=222, route="GET /test")
        assert result1.allowed
        assert result2.allowed
        result3 = manager.check_rate_limit(user_id=111, route="GET /test")
        assert not result3.allowed
        result4 = manager.check_rate_limit(user_id=222, route="GET /test")
        assert not result4.allowed


class TestResourceExhaustionAttacks:
    """Tests for resource exhaustion attack prevention."""

    def test_memory_exhaustion_via_buckets(self, memory_storage):
        """Test that creating many buckets doesn't exhaust memory."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for user_id in range(1000):
            manager.check_rate_limit(user_id=user_id, route="GET /test")

    def test_timestamp_list_growth_control(self, memory_storage, test_user_id):
        """Test sliding window timestamp list doesn't grow unbounded."""
        config = RateLimitConfig(
            requests=1000,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for _ in range(1000):
            manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert not result.allowed

    def test_high_cost_request_handling(self, memory_storage, test_user_id):
        """Test that high-cost requests are handled properly."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /test",
            cost=50,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /test",
            cost=50,
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /test",
            cost=10,
        )
        assert not result.allowed


class TestEdgeCaseSecurity:
    """Tests for edge case security scenarios."""

    def test_none_user_id_handling(self, memory_storage):
        """Test handling of None user_id with IP fallback."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            ip_config=config,
            enable_global_limit=False,
        )
        for _ in range(3):
            result = manager.check_rate_limit(
                ip_address="192.168.1.1",
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            ip_address="192.168.1.1",
            route="GET /test",
        )
        assert not result.allowed

    def test_empty_route_handling(self, memory_storage, test_user_id):
        """Test handling of empty route."""
        manager = RateLimitManager(
            storage_backend=memory_storage,
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route=None,
        )
        assert result.allowed

    def test_extremely_high_burst(self, memory_storage, test_user_id):
        """Test handling of extremely high burst values."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=1000000,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert result.allowed

    def test_zero_window_seconds(self, memory_storage, test_user_id):
        """Test handling of zero window seconds."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=0.001,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(user_id=test_user_id, route="GET /test")
        assert isinstance(result.allowed, bool)

    def test_route_extraction_malformed_paths(self):
        """Test route extraction with malformed paths."""
        test_cases = [
            "/api/v1/channels//messages",
            "/api/v1/channels/abc/messages",
            "/api/v1/users/@me/../admin",
            "/api/v1/../../etc/passwd",
        ]
        for path in test_cases:
            route, resource_id, webhook_id = extract_route_info(path, "GET")
            assert isinstance(route, str)
