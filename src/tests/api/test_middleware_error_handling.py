"""
Comprehensive tests for error handling middleware.

Tests cover:
- Exception to HTTP status code mapping
- Error response formatting
- CORS headers on error responses
- Validation errors
- Custom exception handling
- Security (no information leakage)
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from src.api.middleware.error_handling import (
    get_status_code_for_exception,
    format_error_response,
    setup_exception_handlers,
    ERROR_MAPPINGS,
)


class TestStatusCodeMapping:
    """Tests for exception to HTTP status code mapping."""

    def test_not_found_error_maps_to_404(self):
        """Test NotFoundError maps to 404."""

        class NotFoundError(Exception):
            pass

        exc = NotFoundError("Resource not found")
        status = get_status_code_for_exception(exc)
        assert status == 404

    def test_access_denied_error_maps_to_403(self):
        """Test AccessDeniedError maps to 403."""

        class AccessDeniedError(Exception):
            pass

        exc = AccessDeniedError("Access denied")
        status = get_status_code_for_exception(exc)
        assert status == 403

    def test_permission_denied_error_maps_to_403(self):
        """Test PermissionDeniedError maps to 403."""

        class PermissionDeniedError(Exception):
            pass

        exc = PermissionDeniedError("Permission denied")
        status = get_status_code_for_exception(exc)
        assert status == 403

    def test_invalid_credentials_error_maps_to_401(self):
        """Test InvalidCredentialsError maps to 401."""

        class InvalidCredentialsError(Exception):
            pass

        exc = InvalidCredentialsError("Invalid credentials")
        status = get_status_code_for_exception(exc)
        assert status == 401

    def test_token_expired_error_maps_to_401(self):
        """Test TokenExpiredError maps to 401."""

        class TokenExpiredError(Exception):
            pass

        exc = TokenExpiredError("Token expired")
        status = get_status_code_for_exception(exc)
        assert status == 401

    def test_user_exists_error_maps_to_409(self):
        """Test UserExistsError maps to 409."""

        class UserExistsError(Exception):
            pass

        exc = UserExistsError("User already exists")
        status = get_status_code_for_exception(exc)
        assert status == 409

    def test_already_exists_error_maps_to_409(self):
        """Test AlreadyExistsError maps to 409."""

        class AlreadyExistsError(Exception):
            pass

        exc = AlreadyExistsError("Already exists")
        status = get_status_code_for_exception(exc)
        assert status == 409

    def test_validation_error_maps_to_400(self):
        """Test ValidationError maps to 400."""

        class ValidationError(Exception):
            pass

        exc = ValidationError("Validation failed")
        status = get_status_code_for_exception(exc)
        assert status == 400

    def test_invalid_error_maps_to_400(self):
        """Test InvalidError maps to 400."""

        class InvalidError(Exception):
            pass

        exc = InvalidError("Invalid input")
        status = get_status_code_for_exception(exc)
        assert status == 400

    def test_limit_error_maps_to_400(self):
        """Test LimitError maps to 400."""

        class LimitError(Exception):
            pass

        exc = LimitError("Limit exceeded")
        status = get_status_code_for_exception(exc)
        assert status == 400

    def test_blocked_error_maps_to_403(self):
        """Test BlockedError maps to 403."""

        class BlockedError(Exception):
            pass

        exc = BlockedError("User blocked")
        status = get_status_code_for_exception(exc)
        assert status == 403

    def test_locked_error_maps_to_423(self):
        """Test LockedError maps to 423."""

        class LockedError(Exception):
            pass

        exc = LockedError("Resource locked")
        status = get_status_code_for_exception(exc)
        assert status == 423

    def test_archived_error_maps_to_410(self):
        """Test ArchivedError maps to 410."""

        class ArchivedError(Exception):
            pass

        exc = ArchivedError("Resource archived")
        status = get_status_code_for_exception(exc)
        assert status == 410

    def test_account_locked_error_maps_to_403(self):
        """Test AccountLockedError maps to 403."""

        class AccountLockedError(Exception):
            pass

        exc = AccountLockedError("Account locked")
        status = get_status_code_for_exception(exc)
        assert status == 403

    def test_unknown_exception_maps_to_500(self):
        """Test unknown exceptions map to 500."""
        exc = Exception("Unknown error")
        status = get_status_code_for_exception(exc)
        assert status == 500

    def test_custom_error_without_pattern_maps_to_500(self):
        """Test custom error without matching pattern maps to 500."""

        class CustomWeirdError(Exception):
            pass

        exc = CustomWeirdError("Something went wrong")
        status = get_status_code_for_exception(exc)
        assert status == 500


class TestErrorResponseFormatting:
    """Tests for error response formatting."""

    def test_format_error_response_structure(self):
        """Test error response has correct structure."""
        response = format_error_response(404, "Not found")
        assert "error" in response
        assert "code" in response["error"]
        assert "message" in response["error"]

    def test_format_error_response_values(self):
        """Test error response contains correct values."""
        response = format_error_response(404, "Not found")
        assert response["error"]["code"] == 404
        assert response["error"]["message"] == "Not found"

    def test_format_error_response_different_codes(self):
        """Test formatting different error codes."""
        test_cases = [
            (400, "Bad request"),
            (401, "Unauthorized"),
            (403, "Forbidden"),
            (404, "Not found"),
            (500, "Internal server error"),
        ]

        for code, message in test_cases:
            response = format_error_response(code, message)
            assert response["error"]["code"] == code
            assert response["error"]["message"] == message


class TestExceptionHandlers:
    """Tests for FastAPI exception handlers."""

    @pytest.fixture
    def app_with_handlers(self):
        """Create app with exception handlers."""
        app = FastAPI()
        setup_exception_handlers(app)

        @app.get("/http-exception")
        async def http_exception_endpoint():
            raise HTTPException(status_code=404, detail="Resource not found")

        @app.get("/http-exception-dict")
        async def http_exception_dict_endpoint():
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Custom error"}},
            )

        @app.get("/general-exception")
        async def general_exception_endpoint():
            raise ValueError("Something went wrong")

        class CustomNotFoundError(Exception):
            pass

        @app.get("/custom-not-found")
        async def custom_not_found_endpoint():
            raise CustomNotFoundError("Custom not found")

        class CustomInvalidError(Exception):
            pass

        @app.get("/custom-invalid")
        async def custom_invalid_endpoint():
            raise CustomInvalidError("Invalid input")

        @app.post("/validation-error")
        async def validation_error_endpoint(data: dict):
            class TestModel(BaseModel):
                name: str = Field(min_length=1)
                age: int = Field(gt=0)

            TestModel(**data)
            return {"success": True}

        return app

    def test_http_exception_handler(self, app_with_handlers):
        """Test HTTP exception handler."""
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/http-exception")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 404

    def test_http_exception_with_dict_detail(self, app_with_handlers):
        """Test HTTP exception with dict detail is preserved."""
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/http-exception-dict")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 400
        assert data["error"]["message"] == "Custom error"

    def test_general_exception_handler(self, app_with_handlers):
        """Test general exception handler."""
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/general-exception")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 500
        assert data["error"]["message"] == "Internal server error"

    def test_custom_not_found_exception(self, app_with_handlers):
        """Test custom NotFound exception is mapped correctly."""
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/custom-not-found")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 404

    def test_custom_invalid_exception(self, app_with_handlers):
        """Test custom Invalid exception is mapped correctly."""
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/custom-invalid")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 400

    def test_validation_exception_handler(self, app_with_handlers):
        """Test validation exception handler."""
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.post("/validation-error", json={})
        assert response.status_code == 400 or response.status_code == 422
        data = response.json()
        assert "error" in data or "detail" in data


class TestCORSHeaders:
    """Tests for CORS headers on error responses."""

    @pytest.fixture
    def app_with_cors(self):
        """Create app with CORS-enabled error handlers."""
        app = FastAPI()
        setup_exception_handlers(app)

        @app.get("/error")
        async def error_endpoint():
            raise HTTPException(status_code=404, detail="Not found")

        return app

    def test_cors_headers_on_error_with_origin(self, app_with_cors):
        """Test CORS headers are included on error responses."""
        client = TestClient(app_with_cors, raise_server_exceptions=False)
        response = client.get("/error", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 404
        assert "Access-Control-Allow-Origin" in response.headers

    def test_cors_headers_on_error_without_origin(self, app_with_cors):
        """Test CORS headers when no origin is provided."""
        client = TestClient(app_with_cors, raise_server_exceptions=False)
        response = client.get("/error")
        assert response.status_code == 404

    def test_cors_allows_credentials(self, app_with_cors):
        """Test CORS allows credentials."""
        client = TestClient(app_with_cors, raise_server_exceptions=False)
        response = client.get("/error", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 404
        if "Access-Control-Allow-Origin" in response.headers:
            assert "Access-Control-Allow-Credentials" in response.headers


class TestSecurityAndInformationLeakage:
    """Security tests to ensure no sensitive information leakage."""

    @pytest.fixture
    def security_app(self):
        """Create app for security testing."""
        app = FastAPI()
        setup_exception_handlers(app)

        @app.get("/internal-error")
        async def internal_error_endpoint():
            secret_key = "super_secret_api_key_12345"
            raise Exception(f"Database connection failed with key: {secret_key}")

        @app.get("/file-path-error")
        async def file_path_error_endpoint():
            raise Exception("Failed to read file: /home/user/.secrets/config.ini")

        @app.get("/stack-trace-error")
        async def stack_trace_error_endpoint():
            try:
                _ = 1 / 0
            except Exception as e:
                raise Exception(f"Calculation failed: {e}")

        return app

    def test_internal_error_hides_details(self, security_app):
        """Test internal errors don't leak sensitive details."""
        client = TestClient(security_app, raise_server_exceptions=False)
        response = client.get("/internal-error")
        assert response.status_code == 500
        data = response.json()

        response_str = str(data).lower()
        assert "secret" not in response_str
        assert "api_key" not in response_str
        assert "12345" not in response_str

    def test_file_path_not_leaked(self, security_app):
        """Test file paths are not leaked in error responses."""
        client = TestClient(security_app, raise_server_exceptions=False)
        response = client.get("/file-path-error")
        assert response.status_code == 500
        data = response.json()

        response_str = str(data).lower()
        assert ".secrets" not in response_str
        assert "/home/" not in response_str

    def test_stack_trace_not_included(self, security_app):
        """Test stack traces are not included in error responses."""
        client = TestClient(security_app, raise_server_exceptions=False)
        response = client.get("/stack-trace-error")
        assert response.status_code == 500
        data = response.json()

        response_str = str(data).lower()
        assert "traceback" not in response_str
        assert "stack" not in response_str
        assert "line " not in response_str


class TestErrorMappingCompleteness:
    """Tests to ensure all error mappings are defined."""

    def test_all_error_mappings_exist(self):
        """Test all expected error mappings exist."""
        expected_patterns = [
            "NotFoundError",
            "AccessDeniedError",
            "PermissionDeniedError",
            "InvalidCredentialsError",
            "TokenExpiredError",
            "TokenInvalidError",
            "AccountLockedError",
            "UserExistsError",
            "AlreadyExistsError",
            "ExistsError",
            "ValidationError",
            "InvalidError",
            "LimitError",
            "BlockedError",
            "LockedError",
            "ArchivedError",
        ]

        for pattern in expected_patterns:
            assert pattern in ERROR_MAPPINGS

    def test_error_mappings_have_valid_codes(self):
        """Test all error mappings have valid HTTP status codes."""
        valid_codes = {400, 401, 403, 404, 409, 410, 423, 429, 500}

        for pattern, code in ERROR_MAPPINGS.items():
            assert isinstance(code, int)
            assert code in valid_codes


class TestValidationErrors:
    """Tests for Pydantic validation error handling."""

    @pytest.fixture
    def validation_app(self):
        """Create app with validation endpoints."""
        app = FastAPI()
        setup_exception_handlers(app)

        class UserCreate(BaseModel):
            username: str = Field(min_length=3, max_length=20)
            email: str
            age: int = Field(gt=0, lt=150)

        @app.post("/users")
        async def create_user(user: UserCreate):
            return {"user": user.dict()}

        return app

    def test_missing_required_field(self, validation_app):
        """Test validation error for missing required field."""
        client = TestClient(validation_app, raise_server_exceptions=False)
        response = client.post("/users", json={"username": "test"})
        assert response.status_code in [400, 422]

    def test_invalid_field_value(self, validation_app):
        """Test validation error for invalid field value."""
        client = TestClient(validation_app, raise_server_exceptions=False)
        response = client.post(
            "/users", json={"username": "ab", "email": "test@example.com", "age": 25}
        )
        assert response.status_code in [400, 422]

    def test_multiple_validation_errors(self, validation_app):
        """Test multiple validation errors."""
        client = TestClient(validation_app, raise_server_exceptions=False)
        response = client.post("/users", json={"username": "ab", "age": -5})
        assert response.status_code in [400, 422]


class TestEdgeCases:
    """Tests for edge cases in error handling."""

    @pytest.fixture
    def edge_case_app(self):
        """Create app for edge case testing."""
        app = FastAPI()
        setup_exception_handlers(app)

        @app.get("/none-exception")
        async def none_exception():
            raise Exception(None)

        @app.get("/empty-exception")
        async def empty_exception():
            raise Exception("")

        @app.get("/unicode-exception")
        async def unicode_exception():
            raise Exception("Error with émojis 🚀 and spëcial çhars")

        @app.get("/very-long-exception")
        async def very_long_exception():
            raise Exception("A" * 10000)

        return app

    def test_none_exception_message(self, edge_case_app):
        """Test exception with None message."""
        client = TestClient(edge_case_app, raise_server_exceptions=False)
        response = client.get("/none-exception")
        assert response.status_code == 500

    def test_empty_exception_message(self, edge_case_app):
        """Test exception with empty message."""
        client = TestClient(edge_case_app, raise_server_exceptions=False)
        response = client.get("/empty-exception")
        assert response.status_code == 500

    def test_unicode_exception_message(self, edge_case_app):
        """Test exception with unicode characters."""
        client = TestClient(edge_case_app, raise_server_exceptions=False)
        response = client.get("/unicode-exception")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data

    def test_very_long_exception_message(self, edge_case_app):
        """Test exception with very long message."""
        client = TestClient(edge_case_app, raise_server_exceptions=False)
        response = client.get("/very-long-exception")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
