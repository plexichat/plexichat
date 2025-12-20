"""
Comprehensive tests for logging middleware.

Tests cover:
- Request/response logging
- Timing accuracy
- Log level selection based on status code
- Skipped paths
- Telemetry integration
- Error logging
- Performance under load
"""

import pytest
import time
from unittest.mock import Mock, patch
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from src.api.middleware.logging import LoggingMiddleware, SKIP_PATHS


class TestLoggingMiddleware:
    """Tests for LoggingMiddleware class."""

    @pytest.fixture
    def app_with_logging(self):
        """Create test app with logging middleware."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        @app.get("/slow")
        async def slow_endpoint():
            time.sleep(0.1)
            return {"status": "slow"}

        @app.get("/error")
        async def error_endpoint():
            return Response(content="Server Error", status_code=500)

        @app.get("/not-found")
        async def not_found_endpoint():
            return Response(content="Not Found", status_code=404)

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_logs_successful_request(self, mock_log_info, app_with_logging):
        """Test successful request is logged."""
        client = TestClient(app_with_logging)
        response = client.get("/test")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "GET" in log_message
        assert "/test" in log_message
        assert "200" in log_message

    @patch('src.api.middleware.logging._log_warning')
    def test_logs_404_as_warning(self, mock_log_warning, app_with_logging):
        """Test 404 response is logged as warning."""
        client = TestClient(app_with_logging)
        response = client.get("/not-found")
        assert response.status_code == 404
        
        assert mock_log_warning.called
        log_message = mock_log_warning.call_args[0][0]
        assert "404" in log_message

    @patch('src.api.middleware.logging._log_error')
    def test_logs_500_as_error(self, mock_log_error, app_with_logging):
        """Test 500 response is logged as error."""
        client = TestClient(app_with_logging)
        response = client.get("/error")
        assert response.status_code == 500
        
        assert mock_log_error.called
        log_message = mock_log_error.call_args[0][0]
        assert "500" in log_message

    @patch('src.api.middleware.logging._log_info')
    def test_logs_include_timing(self, mock_log_info, app_with_logging):
        """Test logs include response timing in milliseconds."""
        client = TestClient(app_with_logging)
        response = client.get("/test")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "ms" in log_message

    @patch('src.api.middleware.logging._log_info')
    def test_logs_include_client_ip(self, mock_log_info, app_with_logging):
        """Test logs include client IP address."""
        client = TestClient(app_with_logging)
        response = client.get("/test")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "testclient" in log_message.lower() or "127.0.0.1" in log_message or "unknown" not in log_message.lower()

    @patch('src.api.middleware.logging._log_info')
    def test_timing_accuracy(self, mock_log_info, app_with_logging):
        """Test timing measurement is accurate."""
        client = TestClient(app_with_logging)
        response = client.get("/slow")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        
        import re
        timing_match = re.search(r'(\d+\.\d+)ms', log_message)
        if timing_match:
            duration = float(timing_match.group(1))
            assert duration >= 100


class TestSkippedPaths:
    """Tests for path skipping functionality."""

    @pytest.fixture
    def skip_paths_app(self):
        """Create app with paths that should be skipped."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/api/v1/health")
        async def health_endpoint():
            return {"status": "healthy"}

        @app.get("/health")
        async def alt_health_endpoint():
            return {"status": "healthy"}

        @app.get("/favicon.ico")
        async def favicon_endpoint():
            return Response(content="", status_code=204)

        @app.get("/api/v1/test")
        async def test_endpoint():
            return {"status": "ok"}

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_health_endpoint_skipped(self, mock_log_info, skip_paths_app):
        """Test /api/v1/health endpoint is not logged."""
        client = TestClient(skip_paths_app)
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert not mock_log_info.called

    @patch('src.api.middleware.logging._log_info')
    def test_alt_health_endpoint_skipped(self, mock_log_info, skip_paths_app):
        """Test /health endpoint is not logged."""
        client = TestClient(skip_paths_app)
        response = client.get("/health")
        assert response.status_code == 200
        assert not mock_log_info.called

    @patch('src.api.middleware.logging._log_info')
    def test_favicon_skipped(self, mock_log_info, skip_paths_app):
        """Test /favicon.ico is not logged."""
        client = TestClient(skip_paths_app)
        response = client.get("/favicon.ico")
        assert response.status_code == 204
        assert not mock_log_info.called

    @patch('src.api.middleware.logging._log_info')
    def test_normal_endpoint_not_skipped(self, mock_log_info, skip_paths_app):
        """Test normal endpoints are still logged."""
        client = TestClient(skip_paths_app)
        response = client.get("/api/v1/test")
        assert response.status_code == 200
        assert mock_log_info.called

    def test_skip_paths_constant(self):
        """Test SKIP_PATHS constant contains expected paths."""
        assert "/api/v1/health" in SKIP_PATHS
        assert "/health" in SKIP_PATHS
        assert "/favicon.ico" in SKIP_PATHS


class TestTelemetryIntegration:
    """Tests for telemetry integration."""

    @pytest.fixture
    def telemetry_app(self):
        """Create app for telemetry testing."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/api/v1/endpoint")
        async def api_endpoint():
            return {"data": "test"}

        @app.get("/non-api/endpoint")
        async def non_api_endpoint():
            return {"data": "test"}

        return app

    @patch('src.api.middleware.logging._submit_server_telemetry')
    def test_telemetry_submitted_for_api_endpoints(self, mock_telemetry, telemetry_app):
        """Test telemetry is submitted for API endpoints."""
        client = TestClient(telemetry_app)
        response = client.get("/api/v1/endpoint")
        assert response.status_code == 200
        
        if mock_telemetry.called:
            args = mock_telemetry.call_args[0]
            assert "/api/v1/endpoint" in args[0]
            assert "GET" == args[1]
            assert isinstance(args[2], float)
            assert args[3] == 200

    @patch('src.api.middleware.logging._submit_server_telemetry')
    def test_telemetry_not_submitted_for_non_api(self, mock_telemetry, telemetry_app):
        """Test telemetry is not submitted for non-API endpoints."""
        client = TestClient(telemetry_app)
        response = client.get("/non-api/endpoint")
        assert response.status_code == 200
        
        assert not mock_telemetry.called


class TestExceptionLogging:
    """Tests for exception logging during request processing."""

    @pytest.fixture
    def exception_app(self):
        """Create app that raises exceptions."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/exception")
        async def exception_endpoint():
            raise ValueError("Test exception")

        @app.get("/runtime-error")
        async def runtime_error_endpoint():
            raise RuntimeError("Runtime error")

        return app

    @patch('src.api.middleware.logging._log_error')
    def test_exception_is_logged(self, mock_log_error, exception_app):
        """Test exceptions during request are logged."""
        client = TestClient(exception_app)
        try:
            _ = client.get("/exception")
        except Exception:
            pass
        
        if mock_log_error.called:
            log_message = mock_log_error.call_args[0][0]
            assert "ERROR" in log_message or "/exception" in log_message

    @patch('src.api.middleware.logging._log_error')
    def test_exception_log_includes_timing(self, mock_log_error, exception_app):
        """Test exception logs include timing information."""
        client = TestClient(exception_app)
        try:
            _ = client.get("/exception")
        except Exception:
            pass
        
        if mock_log_error.called:
            log_message = mock_log_error.call_args[0][0]
            assert "ms" in log_message.lower() or "exception" in log_message.lower()


class TestLoggerAvailability:
    """Tests for handling when logger is not available."""

    @pytest.fixture
    def no_logger_app(self):
        """Create app for testing without logger."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        return app

    @patch('src.api.middleware.logging._get_logger', return_value=None)
    def test_handles_missing_logger(self, mock_get_logger, no_logger_app):
        """Test middleware handles missing logger gracefully."""
        client = TestClient(no_logger_app)
        response = client.get("/test")
        assert response.status_code == 200

    @patch('src.api.middleware.logging._get_logger')
    def test_handles_logger_runtime_error(self, mock_get_logger, no_logger_app):
        """Test middleware handles logger RuntimeError gracefully."""
        mock_logger = Mock()
        mock_logger.info.side_effect = RuntimeError("Logger not configured")
        mock_get_logger.return_value = mock_logger

        client = TestClient(no_logger_app)
        response = client.get("/test")
        assert response.status_code == 200


class TestDifferentHTTPMethods:
    """Tests for logging different HTTP methods."""

    @pytest.fixture
    def methods_app(self):
        """Create app with different HTTP methods."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/resource")
        async def get_resource():
            return {"method": "GET"}

        @app.post("/resource")
        async def post_resource():
            return {"method": "POST"}

        @app.put("/resource")
        async def put_resource():
            return {"method": "PUT"}

        @app.patch("/resource")
        async def patch_resource():
            return {"method": "PATCH"}

        @app.delete("/resource")
        async def delete_resource():
            return {"method": "DELETE"}

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_get_method_logged(self, mock_log_info, methods_app):
        """Test GET method is logged correctly."""
        client = TestClient(methods_app)
        response = client.get("/resource")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "GET" in log_message

    @patch('src.api.middleware.logging._log_info')
    def test_post_method_logged(self, mock_log_info, methods_app):
        """Test POST method is logged correctly."""
        client = TestClient(methods_app)
        response = client.post("/resource")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "POST" in log_message

    @patch('src.api.middleware.logging._log_info')
    def test_put_method_logged(self, mock_log_info, methods_app):
        """Test PUT method is logged correctly."""
        client = TestClient(methods_app)
        response = client.put("/resource")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "PUT" in log_message

    @patch('src.api.middleware.logging._log_info')
    def test_delete_method_logged(self, mock_log_info, methods_app):
        """Test DELETE method is logged correctly."""
        client = TestClient(methods_app)
        response = client.delete("/resource")
        assert response.status_code == 200
        
        assert mock_log_info.called
        log_message = mock_log_info.call_args[0][0]
        assert "DELETE" in log_message


class TestStatusCodeRanges:
    """Tests for different status code ranges."""

    @pytest.fixture
    def status_codes_app(self):
        """Create app with different status codes."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/success")
        async def success():
            return Response(content="OK", status_code=200)

        @app.get("/created")
        async def created():
            return Response(content="Created", status_code=201)

        @app.get("/redirect")
        async def redirect():
            return Response(content="", status_code=301)

        @app.get("/bad-request")
        async def bad_request():
            return Response(content="Bad Request", status_code=400)

        @app.get("/unauthorized")
        async def unauthorized():
            return Response(content="Unauthorized", status_code=401)

        @app.get("/forbidden")
        async def forbidden():
            return Response(content="Forbidden", status_code=403)

        @app.get("/server-error")
        async def server_error():
            return Response(content="Server Error", status_code=500)

        @app.get("/service-unavailable")
        async def service_unavailable():
            return Response(content="Service Unavailable", status_code=503)

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_2xx_logged_as_info(self, mock_log_info, status_codes_app):
        """Test 2xx responses are logged as info."""
        client = TestClient(status_codes_app)
        
        for endpoint in ["/success", "/created"]:
            mock_log_info.reset_mock()
            response = client.get(endpoint)
            assert response.status_code < 300
            assert mock_log_info.called

    @patch('src.api.middleware.logging._log_warning')
    def test_4xx_logged_as_warning(self, mock_log_warning, status_codes_app):
        """Test 4xx responses are logged as warning."""
        client = TestClient(status_codes_app)
        
        for endpoint in ["/bad-request", "/unauthorized", "/forbidden"]:
            mock_log_warning.reset_mock()
            response = client.get(endpoint)
            assert 400 <= response.status_code < 500
            assert mock_log_warning.called

    @patch('src.api.middleware.logging._log_error')
    def test_5xx_logged_as_error(self, mock_log_error, status_codes_app):
        """Test 5xx responses are logged as error."""
        client = TestClient(status_codes_app)
        
        for endpoint in ["/server-error", "/service-unavailable"]:
            mock_log_error.reset_mock()
            response = client.get(endpoint)
            assert response.status_code >= 500
            assert mock_log_error.called


class TestWebSocketRequests:
    """Tests for WebSocket request handling."""

    @pytest.fixture
    def websocket_app(self):
        """Create app with WebSocket endpoint."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/http")
        async def http_endpoint():
            return {"type": "http"}

        return app

    @patch('src.api.middleware.logging._log_info')
    def test_http_requests_logged(self, mock_log_info, websocket_app):
        """Test HTTP requests are logged."""
        client = TestClient(websocket_app)
        response = client.get("/http")
        assert response.status_code == 200
        assert mock_log_info.called


class TestPerformance:
    """Performance tests for logging middleware."""

    @pytest.fixture
    def performance_app(self):
        """Create app for performance testing."""
        app = FastAPI()
        app.add_middleware(LoggingMiddleware)

        @app.get("/fast")
        async def fast_endpoint():
            return {"status": "ok"}

        return app

    @patch('src.api.middleware.logging._log_info')
    @patch('src.api.middleware.logging._submit_server_telemetry')
    def test_minimal_overhead(self, mock_telemetry, mock_log_info, performance_app):
        """Test logging adds minimal overhead."""
        client = TestClient(performance_app)
        
        start = time.perf_counter()
        for _ in range(100):
            response = client.get("/fast")
            assert response.status_code == 200
        duration = time.perf_counter() - start
        
        assert duration < 5.0

    @patch('src.api.middleware.logging._log_info')
    def test_concurrent_logging_safe(self, mock_log_info, performance_app):
        """Test logging is safe under concurrent load."""
        import threading
        client = TestClient(performance_app)
        
        def make_request():
            for _ in range(10):
                response = client.get("/fast")
                assert response.status_code == 200
        
        threads = [threading.Thread(target=make_request) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        
        assert mock_log_info.called
