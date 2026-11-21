"""Regression: the cached API config must be copy-on-read/write isolated.

``APIConfig`` is a mutable dataclass whose CORS list fields and
``access_token_required`` flag drive the CORS and access-token gates on every
request. ``_api_config_snapshot`` is process-wide and keyed only by config
version, so handing out the memoized object by reference would let any caller's
in-place edit widen those gates for every later request for the whole version
(and staleness window) -- the same cross-request poisoning class as the
capability-verdict and admin-host caches.

This pins the contract on both paths: the builder stores a value detached from
the source config dict, and every read returns a fresh copy whose mutation
leaves the memo and the downstream CORS decision pristine.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from src.api import config as api_config
from src.api.middleware import security as security_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_api_config_cache():
    api_config.clear_api_config_cache()
    yield
    api_config.clear_api_config_cache()


def test_mutated_api_config_cannot_widen_cors_or_auth_gate():
    first = api_config.get_api_config()
    baseline = asdict(first)
    security_before = security_module._security_policy()

    first.cors_origins.append("*")
    first.cors_allow_methods.append("DELETE")
    first.access_token_required = not baseline["access_token_required"]
    first.max_request_body_size = 1

    # A warm read is detached from the memo and completely pristine.
    second = api_config.get_api_config()
    assert second is not first
    assert asdict(second) == baseline
    assert security_module._security_policy() == security_before
    assert "*" not in second.cors_origins


def test_builder_stores_lists_detached_from_the_source_config(monkeypatch):
    source = {
        "cors_origins": ["https://chat.example"],
        "cors_allow_methods": ["GET"],
        "cors_allow_headers": ["Authorization"],
        "cors_expose_headers": ["ETag"],
        "access_token_required": True,
    }
    monkeypatch.setattr(
        api_config.config,
        "get",
        lambda key, default=None: source if key == "api" else default,
    )

    built = api_config._build_api_config()
    for field in (
        "cors_origins",
        "cors_allow_methods",
        "cors_allow_headers",
        "cors_expose_headers",
    ):
        assert getattr(built, field) is not source[field]

    source["cors_origins"].append("https://evil.example")
    source["cors_allow_methods"].append("TRACE")
    assert built.cors_origins == ["https://chat.example"]
    assert built.cors_allow_methods == ["GET"]
