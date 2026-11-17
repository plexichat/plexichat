"""Webhook writes must not trigger a full cache-keyspace scan.

The webhook write routes used to call ``invalidate_pattern("webhooks:*")``,
but nothing caches under a ``webhooks:`` prefix. That call was an O(keyspace)
Valkey SCAN plus an in-memory sweep on every create/update/delete/regenerate.
State changes are still broadcast through ``emit_webhooks_update``.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes.webhooks import create_webhook
from src.api.schemas.webhooks import WebhookCreateRequest


_ROUTE_SOURCE = (
    Path(__file__).resolve().parents[3] / "src" / "api" / "routes" / "webhooks.py"
).read_text(encoding="utf-8")


@pytest.mark.unit
def test_no_webhook_cache_pattern_invalidation_in_route_source():
    call_lines = [
        line.strip()
        for line in _ROUTE_SOURCE.splitlines()
        if line.strip().startswith("invalidate_pattern(")
    ]
    assert call_lines == []
    assert "from src.core.database.cache import invalidate_pattern" not in _ROUTE_SOURCE


class _FakeWebhooks:
    def __init__(self, webhook):
        self.webhook = webhook
        self.create_calls = []

    def create_webhook(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.webhook


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_webhook_does_not_scan_cache(monkeypatch):
    webhook = SimpleNamespace(
        id=5,
        channel_id=6,
        server_id=7,
        creator_id=9,
        name="hook",
        avatar_url=None,
        token="t" * 20,
        url="https://example.test/hook",
        require_signed_deliveries=False,
        created_at=1_700_000_000_000,
    )
    fake = _FakeWebhooks(webhook)
    monkeypatch.setattr(api, "get_webhooks", lambda: fake)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("webhook write must not scan the cache keyspace")

    monkeypatch.setattr(
        "src.core.database.cache.invalidate_pattern", _fail_if_called, raising=False
    )
    monkeypatch.setattr(
        "src.core.events.gateway_emit.emit_webhooks_update",
        lambda *args, **kwargs: None,
        raising=False,
    )

    result = await create_webhook(
        WebhookCreateRequest(channel_id="6", name="hook"),  # pyright: ignore[reportCallIssue]
        current_user=SimpleNamespace(user_id=9),  # pyright: ignore[reportArgumentType]
    )

    assert result.id == "5"
    assert len(fake.create_calls) == 1
