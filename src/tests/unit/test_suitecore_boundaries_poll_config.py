"""Regression tests for poll duration and close-time config bounds."""

import pytest

from src.core.polls.exceptions import InvalidPollDurationError
from src.core.polls.limits import (
    MAX_DURATION_HOURS,
    MAX_TIMESTAMP_MS,
    MIN_DURATION_HOURS,
    bound_timestamp,
    resolve_field_limits,
)
from src.core.polls.manager.base import PollManagerBase
from src.core.polls.manager.creation import CreationMixin
from src.core.security.hardening import HardeningViolation


class TestDurationLimits:
    def test_defaults(self):
        limits = resolve_field_limits({})
        assert limits.min_duration_hours == MIN_DURATION_HOURS
        assert limits.max_duration_hours == MAX_DURATION_HOURS

    @pytest.mark.parametrize("bad", [0, -1, 10**9, "later", None])
    def test_widened_config_falls_back(self, bad):
        limits = resolve_field_limits(
            {"min_duration_hours": bad, "max_duration_hours": bad}
        )
        assert MIN_DURATION_HOURS <= limits.min_duration_hours <= MAX_DURATION_HOURS
        assert MIN_DURATION_HOURS <= limits.max_duration_hours <= MAX_DURATION_HOURS

    def test_inverted_duration_collapses(self):
        limits = resolve_field_limits(
            {"min_duration_hours": 168, "max_duration_hours": 1}
        )
        assert limits.min_duration_hours == limits.max_duration_hours == 1

    def test_tighter_config_honoured(self):
        limits = resolve_field_limits(
            {"min_duration_hours": 2, "max_duration_hours": 24}
        )
        assert (limits.min_duration_hours, limits.max_duration_hours) == (2, 24)


class TestValidateDuration:
    def _validator(self, config):
        class _Holder:
            _config = config
            _validate_duration = CreationMixin._validate_duration

        return _Holder()._validate_duration

    def test_valid_duration(self):
        assert self._validator({})(24) == 24

    @pytest.mark.parametrize("bad", [0, -1, 169, 10**9, "forever", 1.9])
    def test_invalid_duration_rejected(self, bad):
        with pytest.raises(InvalidPollDurationError):
            self._validator({})(bad)

    def test_config_cannot_widen_duration(self):
        with pytest.raises(InvalidPollDurationError):
            self._validator({"max_duration_hours": 10**9})(MAX_DURATION_HOURS + 1)


class TestLoadConfig:
    def test_hostile_config_is_sanitized(self, monkeypatch):
        import utils.config as config

        monkeypatch.setattr(
            config,
            "get",
            lambda key, default=None: (
                {
                    "max_options": 10**6,
                    "max_question_length": -1,
                    "max_duration_hours": 10**9,
                }
                if key == "polls"
                else default
            ),
        )
        loaded = PollManagerBase._load_config(object.__new__(PollManagerBase))
        assert loaded["max_options"] <= 10
        assert loaded["max_question_length"] >= 1
        assert loaded["max_duration_hours"] <= MAX_DURATION_HOURS

    def test_non_mapping_config_uses_defaults(self, monkeypatch):
        import utils.config as config

        monkeypatch.setattr(config, "get", lambda key, default=None: "nonsense")
        loaded = PollManagerBase._load_config(object.__new__(PollManagerBase))
        assert loaded["min_options"] == 2


class TestCloseTimeBounds:
    @pytest.mark.parametrize("bad", [-1, MAX_TIMESTAMP_MS + 1, 10**20, "ever"])
    def test_bad_timestamp_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            bound_timestamp(bad, "close time")

    def test_valid_timestamp(self):
        assert bound_timestamp(1_700_000_000_000) == 1_700_000_000_000
