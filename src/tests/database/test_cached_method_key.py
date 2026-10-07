"""Regression tests: @cached on methods must not key on the instance.

The bound instance's ``repr`` carries its memory address, so including it in
the default cache key pins each entry to one object: a second instance with
identical arguments can never hit, and every instance mints keys nobody else
reuses. These tests pin the fixed behavior (``self``/``cls`` excluded from
the default key) at the memory tier with Valkey forced off.
"""

import pytest

from src.core.database.cache import decorators
from src.core.database.cache.base import _mem_cache
from src.core.database.cache.decorators import cached


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch):
    """Use the in-memory tier only and start each test with an empty cache."""
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)
    _mem_cache.clear()
    yield
    _mem_cache.clear()


class _WidgetRoutes:
    def __init__(self):
        self.calls = 0

    @cached(ttl=60, prefix="widgets_method")
    def get_widget(self, widget_id: int) -> str:
        self.calls += 1
        return f"widget-{widget_id}"


class _AsyncWidgetRoutes:
    def __init__(self):
        self.calls = 0

    @cached(ttl=60, prefix="widgets_async_method")
    async def get_widget(self, widget_id: int) -> str:
        self.calls += 1
        return f"widget-{widget_id}"


def test_method_cache_shared_across_instances():
    first, second = _WidgetRoutes(), _WidgetRoutes()
    assert first.get_widget(7) == "widget-7"
    assert second.get_widget(7) == "widget-7"
    assert first.calls == 1
    assert second.calls == 0


def test_method_cache_still_partitions_by_arguments():
    first, second = _WidgetRoutes(), _WidgetRoutes()
    assert first.get_widget(7) == "widget-7"
    assert second.get_widget(8) == "widget-8"
    assert first.calls == 1
    assert second.calls == 1


def test_method_cache_key_contains_no_memory_address():
    _WidgetRoutes().get_widget(7)
    keys = [k for k in _mem_cache if k.startswith("widgets_method")]
    assert keys, "expected the method result to be cached"
    assert len(keys) == 1
    assert "object at 0x" not in keys[0]


def test_method_invalidate_clears_shared_entry():
    first, second = _WidgetRoutes(), _WidgetRoutes()
    assert first.get_widget(7) == "widget-7"
    _WidgetRoutes.get_widget.invalidate(second, 7)
    assert second.get_widget(7) == "widget-7"
    assert second.calls == 1


@pytest.mark.asyncio
async def test_async_method_cache_shared_across_instances():
    first, second = _AsyncWidgetRoutes(), _AsyncWidgetRoutes()
    assert await first.get_widget(7) == "widget-7"
    assert await second.get_widget(7) == "widget-7"
    assert first.calls == 1
    assert second.calls == 0


def test_plain_function_keeps_all_arguments_in_key():
    seen = []

    @cached(ttl=60, prefix="widgets_function")
    def get_widget(tag: str, widget_id: int) -> str:
        seen.append((tag, widget_id))
        return f"{tag}-{widget_id}"

    assert get_widget("a", 1) == "a-1"
    assert get_widget("a", 1) == "a-1"
    assert get_widget("b", 1) == "b-1"
    assert seen == [("a", 1), ("b", 1)]
