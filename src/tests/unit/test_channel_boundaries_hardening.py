"""Regression tests for the channel-route request boundaries.

Each guard added during the channel hardening sweep is pinned here. The tests
call the guard callables and read route signatures directly, so a future edit
that drops or loosens a bound fails loudly without booting the whole app.
"""

import inspect

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
        assert exc.value.detail["error"]["code"] == 400

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
            assert "require_channel_snowflake" in unwrapped.__code__.co_names

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
