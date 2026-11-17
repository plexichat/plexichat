"""Regression tests for snowflake ID guards on hardened API routes.

Bare ``int()`` accepted negatives, zero, values past 2**63-1 and forms such
as ``1_2``. Those values reached search backends, notification lookups and
webhook token/signature/storage paths as though they were real identifiers.
Every ID on these routes must now pass the shared snowflake guard and fail
with the endpoint's 400 contract before any backend work is done.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes import notifications as notif_route
from src.api.routes import search as search_route
from src.api.routes import webhooks as webhook_route


class _User:
    def __init__(self, user_id=7):
        self.user_id = user_id


BAD_IDS = [
    "-1",      # negative
    "0",       # zero is not a snowflake
    "9223372036854775808",  # 2**63, past the snowflake envelope
    "9" * 40,  # oversized / integer-churning blob
    "1_2",     # non-canonical integer form
]


class _NoCallSearch:
    def search_messages_page(self, **kwargs):
        raise AssertionError("search backend must not run for an invalid ID")

    def search_users_page(self, **kwargs):
        raise AssertionError("search backend must not run for an invalid ID")


class _NoCallNotifications:
    def mark_notification_read(self, user_id, notification_id):
        raise AssertionError("notification store must not run for an invalid ID")

    def delete_notification(self, user_id, notification_id):
        raise AssertionError("notification store must not run for an invalid ID")


class _NoCallWebhooks:
    def regenerate_token(self, user_id, webhook_id):
        raise AssertionError("webhook store must not run for an invalid ID")

    def get_webhook(self, webhook_id, user_id=None):
        raise AssertionError("webhook store must not run for an invalid ID")

    def delete_webhook(self, user_id, webhook_id):
        raise AssertionError("webhook store must not run for an invalid ID")

    def create_webhook(self, **kwargs):
        raise AssertionError("webhook store must not run for an invalid ID")

    def update_webhook(self, **kwargs):
        raise AssertionError("webhook store must not run for an invalid ID")

    def execute_webhook(self, **kwargs):
        raise AssertionError("webhook store must not run for an invalid ID")


def _expect_status(status, coro):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == status


@pytest.fixture(autouse=True)
def _stub_modules(monkeypatch):
    monkeypatch.setattr(api, "get_search", lambda: _NoCallSearch())
    monkeypatch.setattr(api, "get_notifications", lambda: _NoCallNotifications())
    monkeypatch.setattr(api, "get_webhooks", lambda: _NoCallWebhooks())


@pytest.mark.parametrize("bad", BAD_IDS)
@pytest.mark.parametrize(
    "field", ["conversation_id", "server_id", "channel_id", "author_id"]
)
def test_search_message_filters_reject_non_snowflakes(bad, field):
    kwargs = dict(
        query="hello",
        conversation_id=None,
        server_id=None,
        channel_id=None,
        author_id=None,
        limit=25,
        cursor=None,
        current_user=_User(),
    )
    kwargs[field] = bad
    _expect_status(400, search_route.search_messages(**kwargs))


@pytest.mark.parametrize("bad", BAD_IDS)
def test_search_users_server_filter_rejects_non_snowflakes(bad):
    _expect_status(
        400,
        search_route.search_users(
            query="someone", server_id=bad, limit=25, cursor=None, current_user=_User()
        ),
    )


@pytest.mark.parametrize("bad", BAD_IDS)
def test_notification_routes_reject_non_snowflakes(bad):
    _expect_status(400, notif_route.mark_read(bad, _User()))
    _expect_status(400, notif_route.delete_notification(bad, _User()))


@pytest.mark.parametrize("bad", BAD_IDS)
def test_webhook_routes_reject_non_snowflakes(bad):
    _expect_status(400, webhook_route.regenerate_webhook_token(bad, _User()))
    _expect_status(400, webhook_route.get_webhook(bad, _User()))
    _expect_status(400, webhook_route.delete_webhook(bad, _User()))

    body = SimpleNamespace(
        content=None,
        username=None,
        avatar_url=None,
        embeds=None,
        thread_id=None,
    )
    _expect_status(400, webhook_route.execute_webhook(bad, "token", body))

    create_body = SimpleNamespace(
        channel_id=bad,
        name="x",
        avatar_url=None,
        require_signed_deliveries=False,
    )
    _expect_status(400, webhook_route.create_webhook(create_body, _User()))

    update_body = SimpleNamespace(
        name=None,
        avatar_url=None,
        channel_id=bad,
        require_signed_deliveries=None,
    )
    _expect_status(400, webhook_route.update_webhook("123", update_body, _User()))


@pytest.mark.parametrize("bad", BAD_IDS)
def test_execute_webhook_rejects_non_snowflake_thread_id(bad):
    body = SimpleNamespace(
        content="hi", username=None, avatar_url=None, embeds=None, thread_id=None
    )
    _expect_status(400, webhook_route.execute_webhook("123", "token", body, bad))


def test_valid_snowflakes_still_parse():
    assert search_route._parse_search_id("123", "id") == 123
    assert notif_route._parse_notification_id("123") == 123
    assert webhook_route._parse_webhook_id("123") == 123
    assert webhook_route._parse_webhook_id(123, "id") == 123


def test_suggestions_rate_limited(monkeypatch):
    """Distinct autocomplete prefixes cannot bypass the throttle."""
    import src.core.database as db

    monkeypatch.setattr(db, "check_rate_limit", lambda *a, **k: (False, 0))
    _expect_status(
        429,
        search_route.get_search_suggestions(
            q="a", limit=10, current_user=_User()
        ),
    )
