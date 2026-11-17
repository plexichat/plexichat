"""Cross-cutting regression suite for the channel-route boundaries.

Each earlier hardening commit added a focused test. This module pins the
invariants that span all of them: no bare id parsing is reintroduced, every
list endpoint keeps bounded pagination and allowlisted ordering, and each
request/response boundary guard stays wired into its route.
"""

import inspect
from pathlib import Path

import pytest
from fastapi.params import Query as QueryParam

from src.api.routes.channels import helpers
from src.api.routes.channels.composer import channels_router

CHANNEL_DIR = Path(__file__).resolve().parents[3] / "src/api/routes/channels"


def _source(name):
    return (CHANNEL_DIR / name).read_text()


def _route(method, suffix):
    for route in channels_router.routes:
        if method in getattr(route, "methods", set()) and getattr(
            route, "path"
        ).endswith(suffix):
            return route
    raise AssertionError(f"no {method} route ending in {suffix}")


class TestNoUnsafeIdParsing:
    @pytest.mark.parametrize(
        "name", ["crud.py", "invites.py", "webhooks.py", "ratchet.py"]
    )
    def test_path_ids_are_not_parsed_with_bare_int(self, name):
        source = _source(name)
        assert "int(channel_id)" not in source
        assert "parse_id(channel_id" not in source

    def test_dm_recipient_ids_pass_the_snowflake_guard(self):
        source = _source("helpers.py")
        assert "recipient_id = require_target_user_id(" in source


class TestListEndpointInvariants:
    @pytest.mark.parametrize(
        "suffix", ["/{channel_id}/webhooks", "/{channel_id}/ratchet/intervals"]
    )
    def test_list_routes_keep_bounded_pages_and_order_allowlists(self, suffix):
        route = _route("GET", suffix)
        params = inspect.signature(getattr(route, "endpoint")).parameters
        for name in ("limit", "offset", "sort", "order"):
            assert isinstance(params[name].default, QueryParam), (suffix, name)
        assert any(type(m).__name__ == "Le" for m in params["limit"].default.metadata)
        assert any(type(m).__name__ == "Le" for m in params["offset"].default.metadata)

    def test_pagination_and_order_helpers_are_present(self):
        for name in (
            "require_pagination",
            "require_sort",
            "require_order",
            "bounded_bulk_batch",
        ):
            assert callable(getattr(helpers, name))


class TestBoundaryGuardsStayWired:
    def test_update_route_keeps_the_raw_body_cap(self):
        from src.api.routes.channels.crud import _cap_channel_update_body

        route = _route("PATCH", "/{channel_id}")
        assert _cap_channel_update_body in [
            d.call for d in getattr(route, "dependant").dependencies
        ]

    def test_invite_webhook_and_target_guards_are_called(self):
        assert "require_invite_code" in _source("invites.py")
        assert "require_invite_limits" in _source("invites.py")
        assert "require_target_user_id" in _source("invites.py")
        assert "sanitize_webhook_fields" in _source("webhooks.py")
        assert "bounded_bulk_batch" in _source("invites.py")
        assert "bounded_bulk_batch" in _source("crud.py")

    @pytest.mark.parametrize(
        "bad", ["0", "-1", "-99999999999999999999", "9223372036854775808", "abc", None]
    )
    def test_snowflake_guard_rejects_bad_values(self, bad):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            helpers.require_channel_snowflake(bad)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "bad",
        ["javascript:alert(1)", "ftp://x/y", "data:text/html,x", "https://u:p@x/y"],
    )
    def test_webhook_avatar_guard_rejects_non_http_urls(self, bad):
        assert helpers.sanitize_webhook_fields("ci", bad)[1] is None
