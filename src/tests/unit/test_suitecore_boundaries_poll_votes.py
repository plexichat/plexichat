"""Regression tests for poll vote-selection boundaries.

A selection is attacker-controlled: it has to contain real snowflake option
ids, must not be an arbitrary-length list, and duplicates must collapse before
the id list is interpolated into an ``IN (...)`` placeholder clause.
"""

import pytest

from src.core.polls.exceptions import InvalidPollOptionError
from src.core.polls.limits import MAX_VOTE_SELECTIONS, validate_option_ids
from src.core.polls.manager.voting import VotingMixin


class TestValidateOptionIds:
    def test_parses_strings_and_preserves_order(self):
        ids = ["10", 2, "7"]
        assert validate_option_ids(ids) == [10, 2, 7]

    def test_duplicates_collapse(self):
        assert validate_option_ids(["5", "5", "6"]) == [5, 6]

    @pytest.mark.parametrize("bad", [0, -1, -(10**9), 2**63, "abc", "", None, 1.5])
    def test_invalid_ids_rejected(self, bad):
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids([bad])

    def test_empty_selection_rejected(self):
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids([])
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids(None)

    def test_bulk_selection_capped(self):
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids([str(i) for i in range(1, MAX_VOTE_SELECTIONS + 2)])
        assert validate_option_ids([str(i) for i in range(1, MAX_VOTE_SELECTIONS + 1)])


class TestVotingMixinUsesValidator:
    """The mixin must validate before it builds SQL placeholders."""

    def _caller(self, method):
        class _Caller:
            _config = {}
            vote = VotingMixin.vote
            change_vote = VotingMixin.change_vote

            def get_poll(self, *args):
                raise AssertionError("must not reach the database")

        return getattr(_Caller(), method)

    def test_vote_validates_before_lookup(self):
        with pytest.raises(InvalidPollOptionError):
            self._caller("vote")(1, 2, [-1])

    def test_change_vote_validates_before_lookup(self):
        with pytest.raises(InvalidPollOptionError):
            self._caller("change_vote")(1, 2, [0])


class TestPollRouteUsesGuards:
    def test_route_imports_guards(self):
        import src.api.routes.polls as polls_route

        assert callable(polls_route._parse_poll_id)
        assert callable(polls_route.validate_option_ids)
        with pytest.raises(Exception):
            polls_route._parse_poll_id("-1")
        with pytest.raises(Exception):
            polls_route._parse_poll_id("99000000000000000000")
