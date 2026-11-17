"""Security invariants: workbook/poll operations reject bad identifiers.

Deterministic, data-driven guards for the ids that reach PlexiSum workbook
operations and poll vote operations. No database, no clock, no wall-time
assertions: every case is a static input to the validators the operations call
before they build a query.

* ``validate_artifact_id`` / ``validate_app_id`` / ``validate_artifact_type``
  reject out-of-range snowflakes and ids that are not on the server-owned
  allowlist (an attacker cannot turn an id into a module name or table name).
* ``_authorize`` refuses a workbook to any user without ownership, a grant, or
  server membership, and refuses mutations to view-only/server members.
* ``validate_option_ids`` rejects out-of-range poll option ids and caps the
  selection before it is interpolated into an ``IN (...)`` placeholder clause.
"""

from __future__ import annotations

import pytest

from src.core.polls.exceptions import InvalidPollOptionError
from src.core.polls.limits import MAX_VOTE_SELECTIONS, validate_option_ids
from src.core.security.hardening import HardeningViolation
from src.core.security.hardening.numbers import SNOWFLAKE_MAX
from src.core.suite.boundaries import (
    SUITE_APP_IDS,
    SUITE_ARTIFACT_TYPES,
    validate_app_id,
    validate_artifact_id,
    validate_artifact_type,
)
from src.core.suite.sum._shared import SumPermissionError
from src.core.suite.sum.manager_base import SumManagerBase

pytestmark = [pytest.mark.unit, pytest.mark.security]


class TestArtifactIdsAreBoundedSnowflakes:
    @pytest.mark.parametrize(
        "value",
        [
            0,
            -1,
            -(10**9),
            SNOWFLAKE_MAX + 1,
            2**64,
            1.5,
            -0.5,
            True,
            False,
            None,
            "",
            "abc",
            "0",
            "-7",
            "99000000000000000000",
            "1; DROP TABLE sum_workbooks",
            "1\n2",
            [],
            {},
        ],
    )
    def test_out_of_range_or_malformed_artifact_id_rejected(self, value):
        with pytest.raises(HardeningViolation):
            validate_artifact_id(value)

    @pytest.mark.parametrize("value", [1, "1", 42, "42", SNOWFLAKE_MAX, str(SNOWFLAKE_MAX)])
    def test_real_snowflake_artifact_id_accepted(self, value):
        assert validate_artifact_id(value) == int(value)


class TestUnauthorizedSuiteIdsRejected:
    @pytest.mark.parametrize("app_id", sorted(SUITE_APP_IDS))
    def test_declared_app_id_accepted(self, app_id):
        assert validate_app_id(app_id) == app_id

    @pytest.mark.parametrize(
        "app_id",
        [
            "unknown_app",
            "admin",
            "PlexiSum",  # allowlist is case-sensitive
            "../etc/passwd",
            "__import__",
            "os_system",
            "plexisum;drop",
            "plexisum\n",
            "",
            "ab",  # below the 3-character pattern floor
            "a" * 33,  # above the 32-character pattern ceiling
            "1badapp",  # must start with a letter
            None,
            123,
            ["plexisum"],
        ],
    )
    def test_unauthorized_or_malformed_app_id_rejected(self, app_id):
        with pytest.raises(HardeningViolation):
            validate_app_id(app_id)

    @pytest.mark.parametrize("artifact_type", sorted(SUITE_ARTIFACT_TYPES))
    def test_declared_artifact_type_accepted(self, artifact_type):
        assert validate_artifact_type(artifact_type) == artifact_type

    @pytest.mark.parametrize(
        "artifact_type",
        [
            "unknown",
            "../sum",
            "sum;CURRENT_USER",
            "PlexiSum",
            "",
            None,
            7,
            ["sum"],
        ],
    )
    def test_unauthorized_artifact_type_rejected(self, artifact_type):
        with pytest.raises(HardeningViolation):
            validate_artifact_type(artifact_type)


class _ServersProbe:
    def __init__(self, can_manage: bool):
        self._can_manage = can_manage

    def has_permission(self, user_id, server_id, permission):
        return self._can_manage and permission == "server.manage"


class _AuthorizationProbe:
    """Static stand-in for the PlexiSum mixin's authorization dependencies."""

    authorize = SumManagerBase._authorize

    def __init__(self, grants=(), is_member=False, can_manage=False):
        self._grants = frozenset(grants)
        self._member = is_member
        self._servers = _ServersProbe(can_manage)

    def _grant_permissions(self, workbook_id, user_id):
        return self._grants

    def _is_member(self, user_id, server_id):
        return self._member


_PERSONAL = {"id": 10, "owner_id": 1, "scope": "personal", "server_id": None}
_SERVER = {"id": 11, "owner_id": 1, "scope": "server", "server_id": 7}


class TestWorkbookAuthorizationInvariants:
    @pytest.mark.parametrize(
        "probe,workbook,user_id,mutation",
        [
            (_AuthorizationProbe(), _PERSONAL, 1, False),  # owner
            (_AuthorizationProbe(), _PERSONAL, 1, True),
            (_AuthorizationProbe(grants={"edit"}), _PERSONAL, 2, True),
            (_AuthorizationProbe(grants={"view"}), _PERSONAL, 2, False),
            (_AuthorizationProbe(is_member=True), _SERVER, 2, False),
            (_AuthorizationProbe(is_member=True, can_manage=True), _SERVER, 2, True),
        ],
    )
    def test_authorized_calls_pass(self, probe, workbook, user_id, mutation):
        probe.authorize(workbook, user_id, mutation=mutation)

    @pytest.mark.parametrize(
        "probe,workbook,user_id,mutation",
        [
            (_AuthorizationProbe(), _PERSONAL, 2, False),  # unrelated user
            (_AuthorizationProbe(), _PERSONAL, 2, True),
            (_AuthorizationProbe(), _SERVER, 2, False),  # non-member
            (_AuthorizationProbe(), _SERVER, 2, True),
            (_AuthorizationProbe(grants={"view"}), _PERSONAL, 2, True),  # view-only
            (_AuthorizationProbe(is_member=True), _SERVER, 2, True),  # no manage
        ],
    )
    def test_unauthorized_calls_rejected(self, probe, workbook, user_id, mutation):
        with pytest.raises(SumPermissionError):
            probe.authorize(workbook, user_id, mutation=mutation)


class TestPollOptionIdsAreBounded:
    @pytest.mark.parametrize(
        "bad",
        [
            0,
            -1,
            -(10**9),
            SNOWFLAKE_MAX + 1,
            2**64,
            "0",
            "-5",
            "abc",
            "",
            "99000000000000000000",
            True,
            False,
            None,
            1.5,
            {},
        ],
    )
    def test_out_of_range_option_id_rejected(self, bad):
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids([bad])

    @pytest.mark.parametrize("selection", [[], (), None, "1"])
    def test_empty_or_non_list_selection_rejected(self, selection):
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids(selection)

    def test_bulk_selection_capped(self):
        with pytest.raises(InvalidPollOptionError):
            validate_option_ids([str(i) for i in range(1, MAX_VOTE_SELECTIONS + 2)])

    def test_valid_ids_are_parsed_and_deduplicated_in_order(self):
        assert validate_option_ids(["9", 2, "9", "7"]) == [9, 2, 7]
