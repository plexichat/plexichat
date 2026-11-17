"""Security invariants: feature flags and OAuth grants cannot escalate privilege.

This suite is intentionally static/data-driven: every case is a fixed table of
hostile values fed to the same closed validators the production write paths
use.  No database, network, clock or fixture state is involved, so the result
is identical on every run.

Two escalation families are covered:

* **feature flags** -- a caller-supplied flag key must never reach ``getattr``
  as a dunder/attribute name, and numeric rollout controls are bounded;
* **application grants** -- a client may only receive scopes inside its
  application's configured ceiling, even when the scope is globally valid.
"""

import ast
import inspect
import textwrap

import pytest

from src.core.applications.exceptions import InvalidClientError, InvalidScopeError
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.oauth.scopes import (
    PRIVILEGED_SCOPES,
    VALID_SCOPES,
    require_allowed_scopes,
    require_valid_scopes,
    scopes_are_subset,
    validate_permission_string,
    validate_scopes,
)
from src.core.features import has_feature
from src.core.features.validation import (
    MAX_FLAGS_PER_REQUEST,
    RESERVED_FLAG_KEYS,
    validate_feature_flags,
    validate_feature_key,
    validate_feature_value,
    validate_rollout_percentage,
    validate_tier_limits,
    feature_enabled_for,
)
from src.core.security.hardening import HardeningViolation, SNOWFLAKE_MAX

pytestmark = [pytest.mark.unit, pytest.mark.security]


# ---------------------------------------------------------------------------
# feature flag keys / values
# ---------------------------------------------------------------------------

# Each of these used to be a way to make ``getattr(features, key)`` resolve to
# something other than a declared boolean flag (a class, a method, a module
# global).  A truthy result is a privilege escalation, not a feature check.
ESCALATION_KEYS = [
    "__class__",
    "__dict__",
    "__doc__",
    "__globals__",
    "__init__",
    "__module__",
    "__subclasses__",
    "__getattribute__",
    "GetUserFeatures",
    "1_bad",
    "-feature",
    "has space",
    "dot.name",
    "slash/name",
    "quote'name",
    "",
    "a" * 65,
    "line\nbreak",
]


@pytest.mark.parametrize("key", ESCALATION_KEYS)
def test_escalating_feature_keys_are_rejected(key):
    with pytest.raises(HardeningViolation):
        validate_feature_key(key)
    with pytest.raises(HardeningViolation):
        validate_feature_flags({key: True})


@pytest.mark.parametrize(
    "key", ["can_voice", "alpha_tester", "a", "feature_2", "a" * 64]
)
def test_well_formed_feature_keys_round_trip(key):
    assert validate_feature_key(key) == key


def test_reserved_set_is_non_empty_and_explicit():
    # Guards against someone "fixing" a leak by deleting the deny-list.
    assert {"__class__", "__globals__", "__subclasses__"} <= RESERVED_FLAG_KEYS


@pytest.mark.parametrize(
    "value",
    [1.5, [], {}, object(), -1, 2**31, "x" * 257, "bad\x00value", "tab\tvalue"],
)
def test_out_of_policy_feature_values_are_rejected(value):
    with pytest.raises(HardeningViolation):
        validate_feature_value(value)


@pytest.mark.parametrize("value", [True, False, None, 0, 2**31 - 1, "ok", ""])
def test_in_policy_feature_values_round_trip(value):
    assert validate_feature_value(value) == value


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        ["not", "a", "mapping"],
        "flags",
        {f"flag_{i}": True for i in range(MAX_FLAGS_PER_REQUEST + 1)},
        {"good": True, "__class__": False},
        {"good": 1.25},
    ],
)
def test_feature_flag_mapping_is_bounded_and_closed(mapping):
    with pytest.raises(HardeningViolation):
        validate_feature_flags(mapping)


# ---------------------------------------------------------------------------
# rollout / tier limits: a config typo must not become "grant everyone"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "percentage", [-0.1, -1, 100.1, 1000, "abc", None, float("nan")]
)
def test_rollout_percentage_is_bounded(percentage):
    with pytest.raises(HardeningViolation):
        validate_rollout_percentage(percentage)


@pytest.mark.parametrize("percentage", [0, 100, 12.5, "42"])
def test_rollout_percentage_accepts_valid_values(percentage):
    assert 0.0 <= validate_rollout_percentage(percentage) <= 100.0


@pytest.mark.parametrize(
    "targets",
    [
        [-1],
        [0],
        [SNOWFLAKE_MAX + 1],
        ["abc"],
        [123, "not-an-id"],
        ["1.5"],
        [[1]],
        [None],
    ],
)
def test_rollout_targets_must_be_valid_snowflakes(targets):
    with pytest.raises(HardeningViolation):
        feature_enabled_for(42, rollout_percentage=50, targets=targets)


def test_feature_gate_is_deterministic_and_bounded():
    # Deterministic: same input, same decision (no randomness in rollout).
    for user_id in (1, 7, 42, 999_999, SNOWFLAKE_MAX):
        assert feature_enabled_for(user_id, rollout_percentage=0) is False
        assert feature_enabled_for(user_id, rollout_percentage=100) is True
        assert feature_enabled_for(
            user_id, rollout_percentage=37
        ) == feature_enabled_for(user_id, rollout_percentage=37)
    assert feature_enabled_for(1, rollout_percentage=0, granted=True) is True
    assert feature_enabled_for(1, rollout_percentage=0, targets=[1, 2]) is True
    assert feature_enabled_for(3, rollout_percentage=0, targets=[1, 2]) is False


def test_tier_limits_cannot_be_inflated_by_policy_data():
    with pytest.raises(HardeningViolation):
        validate_tier_limits({"multiplier": 101})
    with pytest.raises(HardeningViolation):
        validate_tier_limits({"max_servers": -5})
    with pytest.raises(HardeningViolation):
        validate_tier_limits({"max_servers": 10**12})
    # -1 is only an operator sentinel, only on the documented fields, and only
    # on the trusted config-loading path.
    with pytest.raises(HardeningViolation):
        validate_tier_limits({"max_servers": -1})
    with pytest.raises(HardeningViolation):
        validate_tier_limits({"multiplier": -1}, allow_unlimited=True)
    assert (
        validate_tier_limits({"max_servers": -1}, allow_unlimited=True)["max_servers"]
        == -1
    )
    assert validate_tier_limits({"unknown_limit": 10**9}) == {}


def test_has_feature_validates_the_key_before_dynamic_lookup():
    tree = ast.parse(textwrap.dedent(inspect.getsource(has_feature)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    key_lines = [
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "validate_feature_key"
    ]
    getattr_lines = [
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "getattr"
    ]
    assert key_lines and getattr_lines
    assert min(key_lines) < min(getattr_lines)


# ---------------------------------------------------------------------------
# application grants / OAuth scopes
# ---------------------------------------------------------------------------

# Globally_valid is irrelevant: these names must never be grantable at all.
ESCALATION_SCOPES = [
    "admin",
    "owner",
    "guilds.admin",
    "applications.owner",
    "bot.all",
    "messages.write",
    "guilds.members.write",
    "webhook.incoming.admin",
    "*",
    "__class__",
    "A" * 65,
    "bad\nscope",
]


@pytest.mark.parametrize("scope", ESCALATION_SCOPES)
def test_unknown_scopes_are_refused(scope):
    with pytest.raises(HardeningViolation):
        require_valid_scopes([scope])
    valid, issues = validate_scopes([scope])
    assert valid is False
    assert issues


def test_every_declared_scope_is_accepted():
    assert require_valid_scopes(sorted(VALID_SCOPES)) == sorted(VALID_SCOPES)
    assert PRIVILEGED_SCOPES <= VALID_SCOPES


@pytest.mark.parametrize(
    "scopes",
    [None, [], "identify", ["identify"] * 21, [123], ["identify\x00"]],
)
def test_scope_requests_are_bounded_and_typed(scopes):
    with pytest.raises(HardeningViolation):
        require_valid_scopes(scopes)


@pytest.mark.parametrize(
    "requested,allowed,expected",
    [
        (["identify"], ["identify"], True),
        (["identify", "bot"], ["identify", "bot"], True),
        (["identify"], ["bot"], False),
        (["bot"], ["identify"], False),
        (["identify", "guilds"], ["identify"], False),
        ([], ["identify"], True),
    ],
)
def test_scope_subset_logic(requested, allowed, expected):
    assert scopes_are_subset(requested, allowed) is expected


def test_application_ceiling_cannot_contain_unknown_scopes():
    with pytest.raises(HardeningViolation):
        require_allowed_scopes(["identify", "admin"])
    assert require_allowed_scopes(["identify", "bot"]) == ["identify", "bot"]


@pytest.mark.parametrize(
    "value", ["-1", "abc", "1.5", "  ", "1" * 21, str(2**64), "1\x002"]
)
def test_permission_bitfields_are_bounded(value):
    with pytest.raises(HardeningViolation):
        validate_permission_string(value)


@pytest.mark.parametrize("value", [None, "0", str(2**64 - 1)])
def test_permission_bitfields_accept_valid_values(value):
    expected = "0" if value is None else str(int(value))
    assert validate_permission_string(value) == expected


class _ScopeRowDB:
    """Minimal DB stub: only the two columns the scope ceiling reads."""

    def __init__(self, raw):
        self.raw = raw

    def fetch_one(self, _sql, _params):
        return {"allowed_scopes": self.raw}


class _MissingRowDB:
    def fetch_one(self, _sql, _params):
        return None


def test_oauth_flow_refuses_scope_escalation_over_application_ceiling():
    flow = OAuth2Flow(_ScopeRowDB('["identify", "guilds"]'), {})
    assert flow._require_scopes_for_app(7, ["identify"]) == ["identify"]
    for requested in (
        ["bot"],
        ["guilds.members.read"],
        ["identify", "messages.read"],
        ["email", "webhook.incoming"],
    ):
        with pytest.raises(InvalidScopeError):
            flow._require_scopes_for_app(7, requested)


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        '{"scope": "identify"}',
        '["identify", "admin"]',
        '["identify", "__class__"]',
        '["identify", 123]',
    ],
)
def test_oauth_flow_fails_closed_on_malformed_scope_ceiling(raw):
    flow = OAuth2Flow(_ScopeRowDB(raw), {})
    with pytest.raises(InvalidScopeError):
        flow._require_scopes_for_app(7, ["identify"])


def test_oauth_flow_treats_legacy_empty_ceiling_as_default_set():
    flow = OAuth2Flow(_ScopeRowDB("[]"), {})
    assert flow._application_allowed_scopes(7) == sorted(VALID_SCOPES)


def test_oauth_flow_rejects_unknown_application():
    flow = OAuth2Flow(_MissingRowDB(), {})
    with pytest.raises(InvalidClientError):
        flow._require_scopes_for_app(7, ["identify"])


def test_oauth_entry_points_enforce_the_application_ceiling():
    for name in ("generate_authorization_url", "validate_authorization_request"):
        source = inspect.getsource(getattr(OAuth2Flow, name))
        assert "_require_scopes_for_app" in source, name

    source = inspect.getsource(OAuth2Flow._require_scopes_for_app)
    assert "scopes_are_subset" in source
    assert "InvalidScopeError" in source

    source = inspect.getsource(OAuth2Flow._application_allowed_scopes)
    assert "require_allowed_scopes" in source
    assert "InvalidScopeError" in source
