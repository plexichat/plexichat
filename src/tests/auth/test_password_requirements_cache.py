"""Password-policy endpoint caching (steady-state cost of a public route)."""

import asyncio
import importlib
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def password_mod(monkeypatch):
    mod = importlib.import_module("src.api.routes.auth.password")
    monkeypatch.setattr(mod, "_password_policy_cache", None)
    monkeypatch.setattr(mod, "_password_policy_cache_key", None)
    return mod


def test_policy_is_built_once_and_reused(password_mod, monkeypatch):
    cfg = {"password": {"min_length": 16}, "accounts": {"minimum_age": 18}}
    monkeypatch.setattr(
        password_mod.config_util,
        "get",
        lambda key, default=None: cfg if key == "authentication" else default,
    )

    with patch.object(
        password_mod, "_build_password_requirements", wraps=password_mod._build_password_requirements
    ) as builder:
        first = asyncio.run(password_mod.get_password_requirements())
        second = asyncio.run(password_mod.get_password_requirements())

    assert first is second
    assert builder.call_count == 1
    assert first.min_length == 16
    assert first.minimum_age == 18


def test_policy_rebuilds_when_config_changes(password_mod, monkeypatch):
    state = {"cfg": {"password": {"min_length": 12}, "accounts": {}}}
    monkeypatch.setattr(
        password_mod.config_util,
        "get",
        lambda key, default=None: state["cfg"] if key == "authentication" else default,
    )

    first = asyncio.run(password_mod.get_password_requirements())
    state["cfg"] = {"password": {"min_length": 20}, "accounts": {}}
    second = asyncio.run(password_mod.get_password_requirements())

    assert first.min_length == 12
    assert second.min_length == 20
    assert first is not second
