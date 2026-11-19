"""Regression: admin automod exempt ID lists accept only bounded snowflakes.

``AutomodRuleCreateRequest`` / ``AutomodRuleUpdateRequest`` used to declare
``exempt_roles`` and ``exempt_channels`` as ``Optional[List[int]]``. Pydantic's
lax ``int`` validator silently accepts ``True`` as ``1``, ``1.0`` as ``1``,
negative integers and arbitrarily large integers. These values are persisted in
the rule row and compared against role/channel snowflakes on every message
check (``channel_id in rule.exempt_channels`` / ``user_role_ids &
set(rule.exempt_roles)``), so a spoofed ``[true]`` could exempt role 1 and an
unbounded list inflated both storage and per-message work.

The server-facing automod schemas already use the bounded snowflake type; this
suite pins the admin-side schemas to the same invariant.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.admin.automod import (
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
)
from src.core.security.hardening.numbers import SNOWFLAKE_MAX

CREATE_BASE = {
    "server_id": 1,
    "name": "rule",
    "rule_type": "keyword",
    "config": {},
    "actions": [],
}

EXEMPT_FIELDS = ("exempt_roles", "exempt_channels")


def _create(field, value):
    return AutomodRuleCreateRequest(**CREATE_BASE, **{field: value})


def _update(field, value):
    return AutomodRuleUpdateRequest(**{field: value})


@pytest.mark.parametrize("builder", [_create, _update])
@pytest.mark.parametrize("field", EXEMPT_FIELDS)
@pytest.mark.parametrize(
    "bad_value",
    [
        [True],
        [False],
        [1.0],
        [-1],
        [SNOWFLAKE_MAX + 1],
        [10**40],
    ],
)
def test_non_snowflake_exempt_ids_are_rejected(builder, field, bad_value):
    with pytest.raises(ValidationError):
        builder(field, bad_value)


@pytest.mark.parametrize("builder", [_create, _update])
@pytest.mark.parametrize("field", EXEMPT_FIELDS)
def test_valid_snowflake_exempt_ids_are_accepted_as_ints(builder, field):
    # Decimal strings are normalized to ints, matching the rest of the API;
    # the values still reach the core automod engine as ints (core SnowflakeID
    # is an int alias), so exemption set-membership keeps working.
    model = builder(field, [1, "123", str(SNOWFLAKE_MAX)])
    assert getattr(model, field) == [1, 123, SNOWFLAKE_MAX]
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in getattr(model, field))


@pytest.mark.parametrize("builder", [_create, _update])
@pytest.mark.parametrize("field", EXEMPT_FIELDS)
def test_exempt_id_list_is_bounded(builder, field):
    # The list is iterated (and persisted) on every message check, so the
    # number of exemptions needs a ceiling just as much as each ID does.
    with pytest.raises(ValidationError):
        builder(field, [1] * 257)
