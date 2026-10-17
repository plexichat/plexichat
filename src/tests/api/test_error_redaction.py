"""Regression tests for M6: mapped exceptions must not echo str(exc) verbatim.

Every type in ERROR_MAPPINGS returns a generic, stable per-type message;
internal validation detail, filesystem paths, SQL fragments, and config
values are logged server-side only. Status codes are unchanged.
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.error_handling import (
    ERROR_MAPPINGS,
    GENERIC_ERROR_MESSAGES,
    _generic_message_for_exception,
    _safe_error_message,
    get_status_code_for_exception,
    setup_exception_handlers,
)

SECRET_DETAIL = "postgres://user:s3cret@db:5432/internal SELECT * FROM users /var/lib/plexichat/db.sqlite3"


def _make_exc(name, module, message=SECRET_DETAIL):
    cls = type(name, (Exception,), {})
    cls.__module__ = module
    return cls(message)


class TestMappedTypesGeneric:
    def test_each_mapped_type_returns_generic_without_detail(self):
        for exc_name, expected_code in ERROR_MAPPINGS.items():
            for module in ("src.core.something", "some_third_party_lib"):
                exc = _make_exc(exc_name, module)
                assert get_status_code_for_exception(exc) == expected_code
                msg = _safe_error_message(exc, expected_code)
                assert msg == GENERIC_ERROR_MESSAGES[exc_name]
                for leaked in ("s3cret", "postgres://", "/var/lib", "SELECT"):
                    assert leaked not in msg

    def test_suffix_match_uses_base_pattern_message(self):
        exc = _make_exc("FooValidationError", "src.core.x")
        assert get_status_code_for_exception(exc) == 400
        assert (
            _safe_error_message(exc, 400) == GENERIC_ERROR_MESSAGES["ValidationError"]
        )
        assert SECRET_DETAIL not in _safe_error_message(exc, 400)

    def test_unmapped_exception_is_generic_500(self):
        exc = _make_exc("TotallyUnknownError", "src.core.x")
        assert get_status_code_for_exception(exc) == 500
        assert _safe_error_message(exc, 500) == "Internal server error"

    def test_status_codes_preserved(self):
        cases = {
            "NotFoundError": 404,
            "ValidationError": 400,
            "RateLimitError": 429,
            "TokenExpiredError": 401,
            "UserExistsError": 409,
            "LockedError": 423,
            "ArchivedError": 410,
            "ChannelFullError": 507,
        }
        for name, code in cases.items():
            assert get_status_code_for_exception(_make_exc(name, "src.core.x")) == code
            assert (
                _generic_message_for_exception(_make_exc(name, "src.core.x"), code)
                != SECRET_DETAIL
            )

    def test_debug_detail_gate_returns_raw_only_when_allowed(self):
        exc = _make_exc("ValidationError", "src.core.x", "raw internal detail")
        assert (
            _safe_error_message(exc, 400, False)
            == GENERIC_ERROR_MESSAGES["ValidationError"]
        )
        assert _safe_error_message(exc, 400, True) == "raw internal detail"


class TestGeneralHandlerRedaction:
    def _client_with(self, exc_name, module="src.core.something"):
        app = FastAPI()
        setup_exception_handlers(app)

        @app.get("/boom")
        async def boom():
            raise _make_exc(exc_name, module)

        return TestClient(app, raise_server_exceptions=False)

    def test_project_validation_error_redacted_but_400(self):
        resp = self._client_with("ValidationError").get("/boom")
        assert resp.status_code == 400
        body = resp.json()["error"]
        assert body["message"] == GENERIC_ERROR_MESSAGES["ValidationError"]
        assert "s3cret" not in resp.text
        assert "/var/lib" not in resp.text
        assert "SELECT" not in resp.text

    def test_third_party_not_found_redacted_but_404(self):
        resp = self._client_with("NotFoundError", "third_party_lib").get("/boom")
        assert resp.status_code == 404
        assert (
            resp.json()["error"]["message"] == GENERIC_ERROR_MESSAGES["NotFoundError"]
        )
        assert "s3cret" not in resp.text

    def test_rate_limit_redacted_but_429(self):
        resp = self._client_with("RateLimitError").get("/boom")
        assert resp.status_code == 429
        assert (
            resp.json()["error"]["message"] == GENERIC_ERROR_MESSAGES["RateLimitError"]
        )
        assert "s3cret" not in resp.text


class TestMalformedPayloadRedaction:
    def test_validation_handler_returns_generic_no_field_echo(self):
        from pydantic import BaseModel

        app = FastAPI()
        setup_exception_handlers(app)

        class Item(BaseModel):
            count: int

        @app.post("/items")
        async def create(item: Item):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        # 'not-an-int' plus an internal-looking path value must not be echoed.
        resp = client.post("/items", json={"count": "not-an-int-/var/lib/secret"})
        assert resp.status_code == 400
        body = resp.json()["error"]
        assert body["message"] == "Request validation failed"
        assert "/var/lib/secret" not in resp.text
        assert "not-an-int" not in resp.text

    def test_middleware_request_validation_is_generic(self):
        import asyncio

        from src.api.middleware.error_handling import ErrorHandlingMiddleware
        from fastapi.exceptions import RequestValidationError

        messages: list = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        def inner(scope, receive_, send_):
            raise RequestValidationError(
                [
                    {
                        "loc": ("body", "count"),
                        "msg": "secret validator detail",
                        "type": "x",
                    }
                ]
            )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/items",
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
        asyncio.run(ErrorHandlingMiddleware(inner)(scope, receive, send))
        body = json.loads(messages[-1]["body"].decode())
        assert body["error"]["message"] == "Request validation failed"
        assert "secret validator detail" not in json.dumps(body)
