"""DSAR export throttle: 1 request/user/day for real users.

Pins both sides of ``DataExportMixin._check_dsar_rate_limit``:
- enforcement: a second request inside the window raises 429;
- self-test bypass: localhost + internal-secret requests never count.

The bypass exists because a daily throttle and a per-minute suite are
fundamentally incompatible; without it the suite could never re-run.
"""

import time
import uuid

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.users.dsar import DataExportMixin


@pytest.fixture
def mixin():
    return DataExportMixin()


def _external_request():
    class _Client:
        host = "203.0.0.1"

    class _Req:
        client = _Client()
        headers = {}

    return _Req()


def _selftest_request():
    secret = f"test-secret-{uuid.uuid4().hex}"
    api.set_internal_secret(secret)

    class _Client:
        host = "127.0.0.1"

    class _Req:
        client = _Client()
        headers = {"X-Plexichat-Internal-Secret": secret}

    return _Req()


@pytest.fixture
def unique_user_id():
    return int(time.time() * 1000) % 1_000_000_000 + uuid.uuid4().int % 1_000_000


def test_second_request_inside_window_429s(mixin, unique_user_id):
    mixin._check_dsar_rate_limit(unique_user_id, request=_external_request())
    with pytest.raises(HTTPException) as exc_info:
        mixin._check_dsar_rate_limit(unique_user_id, request=_external_request())
    assert exc_info.value.status_code == 429


def test_selftest_requests_never_count(mixin, unique_user_id):
    req = _selftest_request()
    try:
        for _ in range(3):
            mixin._check_dsar_rate_limit(unique_user_id, request=req)
    finally:
        api.set_internal_secret(None)


def test_bypass_requires_both_localhost_and_secret(mixin, unique_user_id):
    # Localhost WITHOUT the secret: still throttled.
    secret = f"test-secret-{uuid.uuid4().hex}"
    api.set_internal_secret(secret)
    try:

        class _Client:
            host = "127.0.0.1"

        class _Req:
            client = _Client()
            headers = {"X-Plexichat-Internal-Secret": "wrong-secret"}

        mixin._check_dsar_rate_limit(unique_user_id, request=_Req())
        with pytest.raises(HTTPException) as exc_info:
            mixin._check_dsar_rate_limit(unique_user_id, request=_Req())
        assert exc_info.value.status_code == 429
    finally:
        api.set_internal_secret(None)
