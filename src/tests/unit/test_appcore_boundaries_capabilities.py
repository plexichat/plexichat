"""Bounds and enum checks on the launcher capability surface."""

import inspect
from typing import cast

from src.api.routes.capabilities import (
    CapabilityStateFilter,
    MAX_CAPABILITY_KEYS,
    MAX_DETAIL_KEYS,
    MAX_DETAIL_VALUE_LENGTH,
    MAX_FEATURE_FILTER_LENGTH,
    MAX_MESSAGE_LENGTH,
    bound_capabilities,
    get_capabilities,
)


def _info(state, message="ok", details=None):
    return {
        "state": state,
        "message": message,
        "details": details or {},
    }


class TestCapabilityEnums:
    def test_filter_matches_declared_states(self):
        from src.core.artifacts.capabilities import CapabilityState

        assert {item.value for item in CapabilityStateFilter} == {
            state.value for state in CapabilityState
        }
        assert CapabilityStateFilter("available") is CapabilityStateFilter.AVAILABLE


class TestBoundCapabilities:
    def test_caps_entry_count(self):
        payload = {
            f"feature_{i}": _info("available") for i in range(MAX_CAPABILITY_KEYS + 5)
        }
        assert len(bound_capabilities(payload)) == MAX_CAPABILITY_KEYS

    def test_filters_by_feature_and_state(self):
        payload = {
            "editor": _info("available"),
            "whiteboard": _info("misconfigured"),
        }
        assert set(bound_capabilities(payload, feature="editor")) == {"editor"}
        assert set(
            bound_capabilities(payload, state=CapabilityStateFilter.MISCONFIGURED)
        ) == {"whiteboard"}
        assert bound_capabilities(payload, feature="missing") == {}

    def test_rejects_invalid_filter_values(self):
        payload = {"editor": _info("available")}
        assert (
            bound_capabilities(payload, feature="a" * (MAX_FEATURE_FILTER_LENGTH + 1))
            == {}
        )
        assert bound_capabilities(payload, feature="bad\nname") == {}
        assert (
            bound_capabilities(
                payload, state=cast(CapabilityStateFilter, "not_a_state")
            )
            == {}
        )

    def test_drops_payloads_with_unknown_state(self):
        assert bound_capabilities({"editor": _info("surprise")}) == {}
        assert bound_capabilities({"editor": "not a mapping"}) == {}

    def test_bounds_message_and_details(self):
        payload = {
            "editor": _info(
                "available",
                message="x" * (MAX_MESSAGE_LENGTH + 50),
                details={
                    **{
                        f"safe_{i}": "y" * (MAX_DETAIL_VALUE_LENGTH + 10)
                        for i in range(MAX_DETAIL_KEYS + 3)
                    }
                },
            )
        }
        result = bound_capabilities(payload)["editor"]
        assert len(result["message"]) == MAX_MESSAGE_LENGTH
        assert len(result["details"]) <= MAX_DETAIL_KEYS
        for value in result["details"].values():
            assert len(value) <= MAX_DETAIL_VALUE_LENGTH


class TestRouteSignature:
    def test_query_params_are_bounded(self):
        params = inspect.signature(get_capabilities).parameters
        assert "feature" in params and "state" in params
        assert params["feature"].default is None
        assert params["state"].default is None
        source = inspect.getsource(get_capabilities)
        assert "Query(max_length=MAX_FEATURE_FILTER_LENGTH)" in source
        assert "CapabilityStateFilter" in source
