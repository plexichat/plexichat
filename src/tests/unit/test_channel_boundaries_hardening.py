"""Regression tests for the channel-route request boundaries.

Each guard added during the channel hardening sweep is pinned here. The tests
call the guard callables and read route signatures directly, so a future edit
that drops or loosens a bound fails loudly without booting the whole app.
"""

import pytest
from fastapi import HTTPException

from src.api.routes.channels import ratchet as ratchet_routes
from src.api.routes.channels.helpers import require_channel_snowflake


class TestChannelSnowflakeBounds:
    @pytest.mark.parametrize("bad", ["0", "-1", "99000000000000000000", "abc", "", None])
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
