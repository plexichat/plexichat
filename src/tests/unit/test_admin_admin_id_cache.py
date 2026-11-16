"""A validated admin session is resolved once per request.

Stacked permission gates (an action scope plus ``maintenance``) call
``get_admin_from_token`` twice on the same request. The token cannot change
between those calls, so the verified admin id is memoised on the request and
the second gate skips the duplicate session lookup. Failures are never cached.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes.admin import utils as admin_utils


def _request(token="token123"):
    headers = [(b"authorization", f"Bearer {token}".encode())]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/search/reindex",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


@pytest.mark.unit
def test_admin_id_is_validated_once_per_request(monkeypatch):
    calls = []
    import src.core.admin as admin_core

    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: calls.append(token) or 7,
    )
    request = _request()

    with patch("src.api.is_self_test_request", return_value=False):
        assert admin_utils.get_admin_from_token(request) == 7
        assert admin_utils.get_admin_from_token(request) == 7

    assert calls == ["token123"]


@pytest.mark.unit
def test_failed_validation_is_not_cached(monkeypatch):
    calls = []
    import src.core.admin as admin_core

    def _reject(token):
        calls.append(token)
        return None

    monkeypatch.setattr(admin_core, "validate_session", _reject)
    request = _request()

    with patch("src.api.is_self_test_request", return_value=False):
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                admin_utils.get_admin_from_token(request)
            assert exc.value.status_code == 401

    assert len(calls) == 2


@pytest.mark.unit
def test_cache_is_per_request(monkeypatch):
    calls = []
    import src.core.admin as admin_core

    monkeypatch.setattr(
        admin_core,
        "validate_session",
        lambda token: calls.append(token) or 7,
    )

    with patch("src.api.is_self_test_request", return_value=False):
        assert admin_utils.get_admin_from_token(_request()) == 7
        assert admin_utils.get_admin_from_token(_request()) == 7

    assert len(calls) == 2
