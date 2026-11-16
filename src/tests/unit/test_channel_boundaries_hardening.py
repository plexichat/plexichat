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
