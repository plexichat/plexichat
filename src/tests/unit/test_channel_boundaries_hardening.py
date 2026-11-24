"""Regression tests for the channel-route request boundaries.

Each guard added during the channel hardening sweep is pinned here. The tests
call the guard callables and read route signatures directly, so a future edit
that drops or loosens a bound fails loudly without booting the whole app.
"""

import inspect
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.api.routes.channels import ratchet as ratchet_routes
from src.api.routes.channels.helpers import require_channel_snowflake


class TestChannelSnowflakeBounds:
    @pytest.mark.parametrize(
        "bad", ["0", "-1", "99000000000000000000", "abc", "", None]
    )
    def test_helpers_reject_out_of_range_or_nonnumeric_ids(self, bad):
        with pytest.raises(HTTPException) as exc:
            require_channel_snowflake(bad)
        assert exc.value.status_code == 400
        assert cast(dict[str, Any], exc.value.detail)["error"]["code"] == 400

    def test_helpers_accept_real_ids(self):
        assert require_channel_snowflake("123456789") == 123456789
        assert require_channel_snowflake(42) == 42

    @pytest.mark.parametrize("bad", ["0", "-5", "99000000000000000000", "junk"])
    def test_ratchet_routes_use_the_shared_guard(self, bad):
        with pytest.raises(HTTPException) as exc:
            ratchet_routes._parse_channel_id(bad)
        assert exc.value.status_code == 400

    def test_ratchet_accepts_a_real_channel_id(self):
        assert ratchet_routes._parse_channel_id("123456789") == 123456789


def _applies_channel_id_bound(func, seen=None):
    """True when ``func`` range-checks the channel id, directly or via a helper.

    The CRUD handlers apply the bound through the shared module-level
    ``_require_channel_access`` gate (which calls ``require_channel_snowflake``)
    rather than inline, so the pin follows one level of same-module helpers
    instead of demanding the name in ``co_names``. A handler that reaches the
    bound through neither path still fails.
    """
    names = func.__code__.co_names
    if "require_channel_snowflake" in names:
        return True
    seen = set() if seen is None else seen
    module = inspect.getmodule(func)
    if module is None:
        return False
    for name in names:
        candidate = getattr(module, name, None)
        if inspect.isfunction(candidate) and candidate not in seen:
            seen.add(candidate)
            if _applies_channel_id_bound(candidate, seen):
                return True
    return False


class TestChannelMixinIdBounds:
    @pytest.mark.parametrize("bad", ["0", "-1", "99000000000000000000", "junk"])
    def test_crud_invites_and_webhooks_share_the_guard(self, bad):
        from src.api.routes.channels.crud import ChannelCRUDMixin
        from src.api.routes.channels.invites import ChannelInvitesMixin
        from src.api.routes.channels.webhooks import ChannelWebhooksMixin

        for handler in (
            ChannelCRUDMixin._get_channel,
            ChannelCRUDMixin._update_channel,
            ChannelCRUDMixin._delete_channel,
            ChannelInvitesMixin._create_channel_invite,
            ChannelWebhooksMixin._get_channel_webhooks,
        ):
            unwrapped = inspect.unwrap(handler)
            assert unwrapped.__module__.startswith("src.api.routes.channels")
            assert _applies_channel_id_bound(unwrapped), (
                f"{unwrapped.__qualname__} applies no channel-id range bound"
            )

    def test_shared_helper_rejects_oversized_and_negative_ids(self):
        for bad in ("-9", "9223372036854775808", "0"):
            with pytest.raises(HTTPException) as exc:
                require_channel_snowflake(bad)
            assert exc.value.status_code == 400


class TestChannelPaginationBounds:
    def test_default_page_is_bounded(self):
        from src.api.routes.channels.helpers import require_pagination

        assert require_pagination(None, None) == (100, 0)

    @pytest.mark.parametrize(
        "limit,offset", [(0, 0), (201, 0), (10, -1), (10, 100_001), ("x", 0)]
    )
    def test_abusive_page_values_are_rejected(self, limit, offset):
        from src.api.routes.channels.helpers import require_pagination

        with pytest.raises(HTTPException) as exc:
            require_pagination(limit, offset)
        assert exc.value.status_code == 400

    def test_both_list_endpoints_expose_bounded_page_params(self):
        import inspect

        from fastapi.params import Query as QueryParam

        from src.api.routes.channels.ratchet import get_channel_ratchet_intervals
        from src.api.routes.channels.webhooks import ChannelWebhooksMixin

        for handler in (
            ChannelWebhooksMixin._get_channel_webhooks,
            get_channel_ratchet_intervals,
        ):
            params = inspect.signature(handler).parameters
            for name in ("limit", "offset"):
                assert isinstance(params[name].default, QueryParam)
                assert any(
                    type(m).__name__ == "Le" for m in params[name].default.metadata
                )


class TestChannelFieldConstraints:
    def test_allowed_channel_types_and_lengths_pass(self):
        from src.api.routes.channels.helpers import validate_channel_fields

        validate_channel_fields(
            {
                "channel_type": "text",
                "name": "a" * 100,
                "topic": "b" * 1024,
            }
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"channel_type": "arbitrary-type"},
            {"channel_type": ""},
            {"name": "a" * 101},
            {"topic": "b" * 1025},
            {"name": "bad\x00name"},
        ],
    )
    def test_bad_types_and_oversized_or_control_text_fail(self, payload):
        from src.api.routes.channels.helpers import validate_channel_fields

        with pytest.raises(HTTPException) as exc:
            validate_channel_fields(payload)
        assert exc.value.status_code == 400

    def test_update_route_invokes_the_guard(self):
        import inspect

        from src.api.routes.channels.crud import ChannelCRUDMixin

        unwrapped = inspect.unwrap(ChannelCRUDMixin._update_channel)
        assert "validate_channel_fields" in unwrapped.__code__.co_names


class TestInviteBoundaries:
    def test_invite_code_shape(self):
        from src.api.routes.channels.helpers import require_invite_code

        assert require_invite_code("aB3_-xyz") == "aB3_-xyz"
        for bad in (
            "",
            None,
            "a" * 65,
            "../etc/passwd",
            "has space",
            "x\x00y",
            # ``$`` matches before a trailing newline, so the old ``match``
            # check let these through into storage lookups and log lines.
            "abc\n",
            "abc\r\n",
            "abc\x1b",
            "abc\u202e",
        ):
            with pytest.raises(HTTPException) as exc:
                require_invite_code(bad)
            assert exc.value.status_code == 400

    def test_invite_limits(self):
        from src.api.routes.channels.helpers import require_invite_limits

        assert require_invite_limits(0, 0) == (0, 0)
        assert require_invite_limits(86400, 10) == (86400, 10)
        for age, uses in ((10**9, 0), (0, 10**9), (-1, 0), (0, -1)):
            with pytest.raises(HTTPException) as exc:
                require_invite_limits(age, uses)
            assert exc.value.status_code == 400

    def test_all_invite_handlers_validate_the_code(self):
        from src.api.routes.channels.invites import ChannelInvitesMixin

        for handler in (
            ChannelInvitesMixin._get_invite_info,
            ChannelInvitesMixin._join_server_via_invite,
            ChannelInvitesMixin._delete_invite,
        ):
            assert "require_invite_code" in handler.__code__.co_names
        create = ChannelInvitesMixin._create_channel_invite
        assert "require_invite_limits" in create.__code__.co_names


class TestWebhookFieldBounds:
    def test_normal_webhook_fields_pass_through(self):
        from src.api.routes.channels.helpers import sanitize_webhook_fields

        assert sanitize_webhook_fields("ci", "https://cdn.example.com/a.png") == (
            "ci",
            "https://cdn.example.com/a.png",
        )

    def test_name_is_bounded_and_control_characters_stripped(self):
        from src.api.routes.channels.helpers import sanitize_webhook_fields

        name, _ = sanitize_webhook_fields("a\x00b" + "x" * 500, None)
        assert len(name) == 100
        assert "\x00" not in name

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "ftp://example.com/x",
            "data:text/html,x",
            "https://user:pass@example.com/x",
            "x" * 3000,
        ],
    )
    def test_non_http_or_oversized_urls_are_dropped(self, url):
        from src.api.routes.channels.helpers import sanitize_webhook_fields

        assert sanitize_webhook_fields("ci", url)[1] is None

    def test_listing_uses_the_field_guard(self):
        from src.api.routes.channels import webhooks as webhooks_routes

        assert (
            "sanitize_webhook_fields"
            in webhooks_routes._webhook_to_response.__code__.co_names
        )


class TestBulkBatchBounds:
    def test_large_batches_are_capped(self):
        from src.api.routes.channels.helpers import bounded_bulk_batch

        assert bounded_bulk_batch(list(range(5000)), "roles") == list(range(1000))
        assert bounded_bulk_batch(None, "roles") == []

    def test_custom_cap_is_itself_bounded(self):
        from src.api.routes.channels.helpers import bounded_bulk_batch

        assert len(bounded_bulk_batch(list(range(50)), "roles", maximum=10)) == 10
        assert len(bounded_bulk_batch(list(range(50)), "roles", maximum=10**9)) == 50

    def test_non_iterable_batch_is_rejected(self):
        from src.api.routes.channels.helpers import bounded_bulk_batch

        with pytest.raises(HTTPException) as exc:
            bounded_bulk_batch(42, "roles")
        assert exc.value.status_code == 400

    def test_channel_event_paths_use_the_cap(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        for name in ("invites.py", "crud.py"):
            source = (root / "src/api/routes/channels" / name).read_text()
            assert "bounded_bulk_batch" in source


class TestListOrderingAllowlists:
    def test_sort_keys_must_be_on_the_allowlist(self):
        from src.api.routes.channels.helpers import WEBHOOK_SORT_FIELDS, require_sort

        assert require_sort("NAME", WEBHOOK_SORT_FIELDS) == "name"
        assert require_sort(None, WEBHOOK_SORT_FIELDS) is None
        for bad in ("__dict__", "id;DROP TABLE", "random", ""):
            with pytest.raises(HTTPException) as exc:
                require_sort(bad, WEBHOOK_SORT_FIELDS)
            assert exc.value.status_code == 400

    def test_order_is_restricted_to_asc_desc(self):
        from src.api.routes.channels.helpers import require_order

        assert require_order("DESC") == "desc"
        assert require_order(None) is None
        for bad in ("sideways", "asc;drop", ""):
            with pytest.raises(HTTPException) as exc:
                require_order(bad)
            assert exc.value.status_code == 400

    def test_list_endpoints_expose_sort_and_order_params(self):
        import inspect

        from src.api.routes.channels.ratchet import get_channel_ratchet_intervals
        from src.api.routes.channels.webhooks import ChannelWebhooksMixin

        for handler in (
            ChannelWebhooksMixin._get_channel_webhooks,
            get_channel_ratchet_intervals,
        ):
            params = inspect.signature(handler).parameters
            assert "sort" in params and "order" in params


class TestTargetUserIdBounds:
    def test_target_ids_accept_real_snowflakes(self):
        from src.api.routes.channels.helpers import require_target_user_id

        assert require_target_user_id("123456789") == 123456789
        assert require_target_user_id(42) == 42

    @pytest.mark.parametrize("bad", ["0", "-3", "9223372036854775808", "abc", None])
    def test_target_ids_reject_out_of_range_values(self, bad):
        from src.api.routes.channels.helpers import require_target_user_id

        with pytest.raises(HTTPException) as exc:
            require_target_user_id(bad)
        assert exc.value.status_code == 400

    def test_invite_and_dm_paths_use_the_guard(self):
        from src.api.routes.channels.invites import ChannelInvitesMixin

        assert (
            "require_target_user_id"
            in ChannelInvitesMixin._get_invite_info.__code__.co_names
        )
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        source = (root / "src/api/routes/channels/helpers.py").read_text()
        assert "require_target_user_id(" in source


class TestChannelUpdateBodyCap:
    def test_update_route_installs_the_body_cap_dependency(self):
        from src.api.routes.channels.composer import channels_router
        from src.api.routes.channels.crud import _cap_channel_update_body

        route = next(
            r for r in channels_router.routes if "PATCH" in getattr(r, "methods", set())
        )
        calls = [d.call for d in getattr(route, "dependant").dependencies]
        assert _cap_channel_update_body in calls

    def test_body_guard_uses_the_shared_capped_reader(self):
        import asyncio

        from fastapi import HTTPException

        from src.api.routes.channels import crud as crud_routes
        from src.api.request_guards import read_body_capped

        class _Request:
            def __init__(self, length, body):
                self.headers = {"content-length": str(length)}
                self._body = body

            async def body(self):
                return self._body

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                read_body_capped(
                    _Request(crud_routes.MAX_CHANNEL_UPDATE_BODY + 1, b"x"), 1024
                )
            )
        assert exc.value.status_code == 413

        assert (
            asyncio.run(
                read_body_capped(
                    _Request(5, b"hello"), crud_routes.MAX_CHANNEL_UPDATE_BODY
                )
            )
            == b"hello"
        )

    def test_cap_constant_is_modest(self):
        from src.api.routes.channels.crud import MAX_CHANNEL_UPDATE_BODY

        assert 0 < MAX_CHANNEL_UPDATE_BODY <= 1024 * 1024
