"""Regression: self-test must send valid sort/order for channel list routes.

The OpenAPI-driven self-test enumerates every declared query parameter and
resolves it through ``ParamValueMixin.get_param_value``. The generic
placeholder value for ``sort``/``order`` was ``"1"``, which the hardened
channel list routes reject with 400 (``require_sort``/``require_order``
allowlists). As a result ``GET /channels/{id}/ratchet/intervals`` and
``GET /channels/{id}/webhooks`` always failed in the self-test even though
the channel id itself was valid. The generator must emit values that the
real boundary guards accept, without relaxing those guards.
"""

from types import SimpleNamespace

import pytest

from src.api.routes.channels.helpers import (
    RATCHET_SORT_FIELDS,
    WEBHOOK_SORT_FIELDS,
    require_order,
    require_sort,
)
from src.core.selftest.services.data import DataGenerator

RATCHET_PATH = "/api/v1/channels/{channel_id}/ratchet/intervals"
WEBHOOK_PATH = "/api/v1/channels/{channel_id}/webhooks"


@pytest.mark.unit
class TestChannelListSortOrderParams:
    @staticmethod
    def _generator():
        ctx = SimpleNamespace(
            _test_password="selftest-pw",
            config={},
            test_channel_id=123,
            test_user_id=1,
            test_server_id=2,
            test_conversation_id=None,
            test_webhook_id=None,
            test_message_id=None,
        )
        return DataGenerator(ctx)

    @pytest.mark.parametrize("path", [RATCHET_PATH, WEBHOOK_PATH])
    def test_generated_sort_value_is_allowlisted_for_both_routes(self, path):
        value = self._generator().get_param_value("sort", path)
        assert value != "1", "generic placeholder would 400 at the route"
        assert require_sort(value, RATCHET_SORT_FIELDS) == value
        assert require_sort(value, WEBHOOK_SORT_FIELDS) == value

    @pytest.mark.parametrize("path", [RATCHET_PATH, WEBHOOK_PATH])
    def test_generated_order_value_is_accepted(self, path):
        value = self._generator().get_param_value("order", path)
        assert value != "1", "generic placeholder would 400 at the route"
        assert require_order(value) == value
