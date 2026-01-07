"""
API endpoint performance and load tests.

Tests API critical paths:
- Endpoint response times
- Concurrent request handling
- Throughput under load
- Rate limiting performance
- Request parsing overhead
- Memory leaks in request handling
"""

import pytest
import concurrent.futures
from datetime import datetime
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def test_app(modules):
    """Create FastAPI test application."""
    from src.api.app import create_app
    import src.api as api

    api._auth = modules.auth
    api._messaging = modules.messaging
    api._servers = modules.servers
    api._presence = modules.presence
    api._relationships = modules.relationships

    app = create_app()
    return app


@pytest.fixture
def api_client(test_app):
    """Create test client."""
    return TestClient(test_app)


class TestAPIEndpointPerformance:
    """Test API endpoint performance."""

    def test_health_endpoint_performance(self, benchmark, api_client):
        """Benchmark health check endpoint."""

        def health_check():
            response = api_client.get("/api/v1/health")
            return response

        response = benchmark(health_check)
        assert response.status_code == 200

    def test_register_endpoint_performance(self, benchmark, api_client):
        """Benchmark registration endpoint."""
        counter = [0]

        def register():
            username = f"apiuser_{counter[0]}"
            email = f"{username}@example.com"
            counter[0] += 1

            response = api_client.post(
                "/api/v1/auth/register",
                json={
                    "username": username,
                    "email": email,
                    "password": "SecurePassword123!@#",
                },
            )
            return response

        response = benchmark(register)
        assert response.status_code == 200
        assert "token" in response.json()

    def test_login_endpoint_performance(self, benchmark, api_client, modules):
        """Benchmark login endpoint."""
        username = "loginapi_user"
        email = f"{username}@example.com"
        password = "SecurePassword123!@#"
        modules.auth.register(username=username, email=email, password=password)

        def login():
            response = api_client.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )
            return response

        response = benchmark(login)
        assert response.status_code == 200
        assert "token" in response.json()

    def test_get_messages_endpoint_performance(
        self, benchmark, api_client, modules, test_dm
    ):
        """Benchmark get messages endpoint."""
        dm, user1, user2 = test_dm

        for i in range(50):
            modules.messaging.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"API test message {i}"
            )

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token

        def get_messages():
            response = api_client.get(
                f"/api/v1/channels/{dm.id}/messages",
                headers={"Authorization": f"Bearer {token}"},
            )
            return response

        response = benchmark(get_messages)
        assert response.status_code == 200
        assert len(response.json()) > 0

    def test_send_message_endpoint_performance(
        self, benchmark, api_client, modules, test_dm
    ):
        """Benchmark send message endpoint."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token

        counter = [0]

        def send_message():
            response = api_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": f"Benchmark message {counter[0]}"},
            )
            counter[0] += 1
            return response

        response = benchmark(send_message)
        assert response.status_code == 200

    def test_concurrent_api_requests(self, benchmark, api_client, modules, test_dm):
        """Test concurrent API request handling."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        def concurrent_requests():
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(
                        api_client.post,
                        f"/api/v1/channels/{dm.id}/messages",
                        headers=headers,
                        json={"content": f"Concurrent message {i}"},
                    )
                    for i in range(50)
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            return results

        responses = benchmark(concurrent_requests)
        assert len(responses) == 50
        assert all(r.status_code == 200 for r in responses)


class TestAPIThroughput:
    """Test API throughput and sustained load."""

    def test_sustained_request_throughput(self, api_client, modules, test_dm):
        """Test sustained request throughput."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        total_requests = 500
        start = datetime.now()

        for i in range(total_requests):
            response = api_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers=headers,
                json={"content": f"Throughput test {i}"},
            )
            assert response.status_code == 200

        elapsed = (datetime.now() - start).total_seconds()
        throughput = total_requests / elapsed

        assert throughput > 50, (
            f"Throughput too low: {throughput:.1f} req/s (expected > 50 req/s)"
        )

    def test_read_heavy_throughput(self, api_client, modules, test_dm):
        """Test throughput for read-heavy operations."""
        dm, user1, user2 = test_dm

        for i in range(100):
            modules.messaging.send_message(
                user_id=user1.id, conversation_id=dm.id, content=f"Message {i}"
            )

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        total_requests = 200
        start = datetime.now()

        for _ in range(total_requests):
            response = api_client.get(
                f"/api/v1/channels/{dm.id}/messages", headers=headers
            )
            assert response.status_code == 200

        elapsed = (datetime.now() - start).total_seconds()
        throughput = total_requests / elapsed

        assert throughput > 100, (
            f"Read throughput too low: {throughput:.1f} req/s (expected > 100 req/s)"
        )

    def test_mixed_workload_throughput(self, api_client, modules, test_dm):
        """Test throughput with mixed read/write operations."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        total_requests = 300
        start = datetime.now()

        for i in range(total_requests):
            if i % 3 == 0:
                response = api_client.post(
                    f"/api/v1/channels/{dm.id}/messages",
                    headers=headers,
                    json={"content": f"Write {i}"},
                )
            else:
                response = api_client.get(
                    f"/api/v1/channels/{dm.id}/messages", headers=headers
                )

            assert response.status_code == 200

        elapsed = (datetime.now() - start).total_seconds()
        throughput = total_requests / elapsed

        assert throughput > 60, (
            f"Mixed throughput too low: {throughput:.1f} req/s (expected > 60 req/s)"
        )


class TestAPIMemory:
    """Test API memory usage and leaks."""

    def test_request_handling_memory_leak(
        self, api_client, modules, test_dm, memory_tracker
    ):
        """Check for memory leaks in request handling."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        initial_memory = memory_tracker.snapshot()

        for i in range(500):
            api_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers=headers,
                json={"content": f"Memory test {i}"},
            )

            if i % 100 == 0:
                memory_tracker.snapshot()

        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory

        assert memory_increase < 50, (
            f"Memory increased by {memory_increase}MB, potential leak"
        )

    def test_authentication_memory_leak(self, api_client, modules, memory_tracker):
        """Check for memory leaks in authentication."""
        username = "authmem_user"
        email = f"{username}@example.com"
        password = "SecurePassword123!@#"
        modules.auth.register(username=username, email=email, password=password)

        initial_memory = memory_tracker.snapshot()

        for i in range(200):
            api_client.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )

            if i % 40 == 0:
                memory_tracker.snapshot()

        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory

        assert memory_increase < 30, (
            f"Memory increased by {memory_increase}MB, potential leak"
        )


class TestAPIDegradation:
    """Test API performance degradation under load."""

    def test_response_time_stability(self, api_client, modules, test_dm):
        """Ensure response times remain stable over time."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        times = []

        for i in range(200):
            start = datetime.now()
            api_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers=headers,
                json={"content": f"Stability test {i}"},
            )
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)

        first_batch_avg = sum(times[:20]) / 20
        last_batch_avg = sum(times[-20:]) / 20

        degradation = (last_batch_avg - first_batch_avg) / first_batch_avg

        assert degradation < 0.5, f"Performance degraded by {degradation * 100:.1f}%"

    def test_concurrent_load_scaling(self, api_client, modules, test_dm):
        """Test how API scales with concurrent load."""
        dm, user1, user2 = test_dm

        login_result = modules.auth.login(user1.username, "TestPass123!")
        token = login_result.token
        headers = {"Authorization": f"Bearer {token}"}

        def load_test(workers, requests_per_worker):
            start = datetime.now()

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        api_client.post,
                        f"/api/v1/channels/{dm.id}/messages",
                        headers=headers,
                        json={"content": f"Load test {i}"},
                    )
                    for i in range(requests_per_worker)
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

            elapsed = (datetime.now() - start).total_seconds()
            return elapsed, len(results)

        time_1, count_1 = load_test(1, 20)
        time_5, count_5 = load_test(5, 20)
        time_10, count_10 = load_test(10, 20)

        assert count_1 == 20
        assert count_5 == 20
        assert count_10 == 20

        assert time_5 < time_1, "Concurrent execution should be faster"
        assert time_10 < time_1, "More concurrency should improve performance"
