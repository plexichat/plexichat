"""Cross-cutting regression suite for the PlexiCore boundary hardening campaign.

The per-area suites live next to the change they cover; this module is the
single guard that fails if a future edit silently unwires one of them. It
checks three things for every campaign area: the hard constants exist and are
sane, hostile input is actually rejected at the boundary, and the production
call sites still import/use the shared guard rather than a local copy.
"""

import inspect

import pytest

from src.core.security.hardening import HardeningViolation

# -- poll boundaries ---------------------------------------------------------
from src.core.polls.limits import (
    MAX_OPTIONS,
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
    MAX_VOTE_SELECTIONS,
    bound_option_count,
    bound_timestamp,
    resolve_field_limits,
    validate_option_ids,
)
from src.core.polls.exceptions import (
    InvalidPollDurationError,
    InvalidPollOptionError,
    InvalidPollQuestionError,
    PollOptionLimitError,
)

# -- plexijoin boundaries -----------------------------------------------------
from src.core.plexijoin.validation import (
    MAX_PARTICIPANTS_PER_ROOM,
    bound_id,
    bound_pagination,
    validate_join_code,
    validate_nonce,
    validate_room_payload,
    validate_shared_key,
)

# -- suite boundaries ---------------------------------------------------------
from src.core.suite.boundaries import (
    MAX_CSV_IMPORT_BYTES,
    MAX_FORMULA_LENGTH,
    MAX_WORKSPACE_PAYLOAD_BYTES,
    SHARED_CONFIG_SCHEMA,
    SUITE_APP_IDS,
    SUITE_ARTIFACT_TYPES,
    bound_formula_text,
    bound_workspace_tags,
    bound_workspace_text,
    validate_app_id,
    validate_artifact_id,
    validate_artifact_type,
    validate_shared_config,
)


class TestConstantsAreSane:
    @pytest.mark.parametrize(
        "value",
        [
            MAX_OPTIONS,
            MAX_OPTION_LENGTH,
            MAX_QUESTION_LENGTH,
            MAX_VOTE_SELECTIONS,
            MAX_PARTICIPANTS_PER_ROOM,
            MAX_CSV_IMPORT_BYTES,
            MAX_WORKSPACE_PAYLOAD_BYTES,
            MAX_FORMULA_LENGTH,
        ],
    )
    def test_positive_finite_cap(self, value):
        assert isinstance(value, int) and 0 < value < 2**63

    def test_catalog_allowlists_nonempty(self):
        assert SUITE_APP_IDS and SUITE_ARTIFACT_TYPES
        assert SHARED_CONFIG_SCHEMA


class TestPollBoundaries:
    def test_hostile_field_values(self):
        with pytest.raises(InvalidPollQuestionError):
            from src.core.polls.limits import validate_question_text

            validate_question_text("x" * (MAX_QUESTION_LENGTH + 1), {})
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids([-1])
        with pytest.raises(PollOptionLimitError):
            bound_option_count(["x"] * (MAX_OPTIONS + 1), {"max_options": 10**9})

    def test_hostile_config_and_timestamps(self):
        limits = resolve_field_limits(
            {
                "max_options": 10**9,
                "max_question_length": -1,
                "max_duration_hours": 10**9,
            }
        )
        assert limits.max_options <= MAX_OPTIONS
        assert limits.max_question_length >= 1
        with pytest.raises(HardeningViolation):
            bound_timestamp(-1)
        from src.core.polls.manager.creation import CreationMixin

        holder = type(
            "_H",
            (),
            {"_config": {}, "_validate_duration": CreationMixin._validate_duration},
        )()
        with pytest.raises(InvalidPollDurationError):
            holder._validate_duration(10**9)


class TestPlexiJoinBoundaries:
    def test_hostile_fields_ids_tokens_room(self):
        with pytest.raises(HardeningViolation):
            bound_id(-1)
        with pytest.raises(HardeningViolation):
            bound_pagination(1, 10**9)
        with pytest.raises(HardeningViolation):
            validate_nonce("bad nonce!")
        with pytest.raises(HardeningViolation):
            validate_join_code("short")
        with pytest.raises(HardeningViolation):
            validate_shared_key("")
        with pytest.raises(HardeningViolation):
            validate_room_payload(
                {
                    "room_id": "r",
                    "participants": [{"id": "u"}] * (MAX_PARTICIPANTS_PER_ROOM + 1),
                }
            )


class TestSuiteBoundaries:
    def test_hostile_workspace_fields(self):
        with pytest.raises(HardeningViolation):
            bound_workspace_text("a\nb", "title")
        with pytest.raises(HardeningViolation):
            bound_workspace_tags(["x"] * 101)
        with pytest.raises(HardeningViolation):
            bound_formula_text("(" * 200)

    def test_hostile_ids_and_config(self):
        for bad in ("../../etc/passwd", "__import__", ""):
            with pytest.raises(HardeningViolation):
                validate_app_id(bad)
        with pytest.raises(HardeningViolation):
            validate_artifact_type("os.system")
        with pytest.raises(HardeningViolation):
            validate_artifact_id(-1)
        cleaned = validate_shared_config({"secret": "x", "retention_days": -1})
        assert "secret" not in cleaned
        assert (
            cleaned["retention_days"] == SHARED_CONFIG_SCHEMA["retention_days"].default
        )


class TestCallSitesStillUseGuards:
    """Source-level checks so nobody can reintroduce a bare coercion."""

    def test_poll_creation_and_voting(self):
        from src.core.polls.manager import creation, voting

        assert "validate_question_text" in inspect.getsource(creation.CreationMixin)
        assert "validate_option_ids" in inspect.getsource(voting.VotingMixin)

    def test_poll_route_uses_parse_id(self):
        import src.api.routes.polls as route

        source = inspect.getsource(route)
        assert "parse_id" in source
        assert "validate_option_ids" in source
        assert "int(poll_id)" not in source

    def test_plexijoin_manager_uses_validation(self):
        from src.core.plexijoin import manager

        source = inspect.getsource(manager.PlexiJoinManager)
        assert "bound_id" in source
        assert "validate_nonce" in source
        assert "validate_room_payload" in source

    def test_suite_apps_use_boundaries(self):
        from src.core.suite.registry import service as registry
        from src.core.suite.stash import cleanup
        from src.core.suite.sum import _formula_shared, manager_workbooks

        assert "validate_app_id" in inspect.getsource(registry)
        assert "validate_shared_config" in inspect.getsource(cleanup)
        assert "bound_formula_text" in inspect.getsource(_formula_shared.parse_formula)
        assert "bound_workspace_text" in inspect.getsource(manager_workbooks)
