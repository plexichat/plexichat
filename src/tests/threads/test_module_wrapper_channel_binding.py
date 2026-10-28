"""The module-level thread wrappers must accept what the routes pass.

``POST /api/v1/channels/{channel_id}/threads`` calls
``threads.create_thread_from_message(..., expected_channel_id=cid)``. The
wrapper in ``src.core.threads`` did not declare that keyword, so every
reply-thread creation answered 500 with a TypeError before any thread logic
ran. These tests pin the wrapper's signature and its forwarding, which the
manager-level tests below it cannot see.
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock

import pytest

from src.core import threads as threads_module


def test_wrapper_accepts_expected_channel_id():
    params = inspect.signature(threads_module.create_thread_from_message).parameters
    assert "expected_channel_id" in params, (
        "the HTTP route passes expected_channel_id; the wrapper must declare it"
    )
    assert params["expected_channel_id"].default is None


def test_wrapper_forwards_expected_channel_id(monkeypatch):
    manager = Mock()
    manager.create_thread_from_message.return_value = object()
    monkeypatch.setattr(threads_module, "_get_manager", lambda: manager)

    threads_module.create_thread_from_message(1, 2, "Thread", expected_channel_id=99)

    _args, kwargs = manager.create_thread_from_message.call_args
    forwarded = kwargs.get("expected_channel_id")
    if forwarded is None and len(_args) >= 6:
        forwarded = _args[5]
    assert forwarded == 99, "the wrapper dropped expected_channel_id on the way down"


def test_wrapper_without_expected_channel_id_keeps_the_old_call_shape(monkeypatch):
    manager = Mock()
    manager.create_thread_from_message.return_value = object()
    monkeypatch.setattr(threads_module, "_get_manager", lambda: manager)

    threads_module.create_thread_from_message(1, 2, "Thread")

    _args, kwargs = manager.create_thread_from_message.call_args
    assert kwargs.get("expected_channel_id") in (None,)
    if len(_args) >= 6:
        assert _args[5] is None


@pytest.mark.parametrize("name", ["create_thread", "create_thread_from_message"])
def test_manager_wrapper_pair_stays_in_step(name):
    """Both wrappers must expose the same optional channel binding."""
    params = inspect.signature(getattr(threads_module, name)).parameters
    assert "expected_channel_id" in params or name == "create_thread"
