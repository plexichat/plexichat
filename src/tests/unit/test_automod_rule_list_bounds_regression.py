"""Regression: server-facing automod rule request lists must be bounded.

The admin-side automod schemas already cap exempt-id lists, but the
server-facing ``AutomodRuleCreateRequest`` / ``AutomodRuleUpdateRequest``
declared ``actions``, ``applied_roles``, ``exempt_roles`` and
``exempt_channels`` as plain unbounded lists. The global 10 MB body cap is
not a field bound: a body just under it can still carry millions of entries,
which are parsed by Pydantic, JSON-encoded into the rule row, scanned on every
message check and replayed into matchers on every worker start.

These tests pin the field ceilings at the HTTP boundary so an oversized list
is rejected with a 422 instead of being persisted and amplified downstream.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.servers import (
    MAX_AUTOMOD_ACTIONS,
    MAX_AUTOMOD_ID_LIST,
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
)

ACTION = {"action_type": "delete_message"}
CREATE_BASE = {
    "name": "rule",
    "rule_type": "keyword",
    "config": {},
    "actions": [ACTION],
}
LIST_FIELDS = ("actions", "applied_roles", "exempt_roles", "exempt_channels")


def _value_for(field, count):
    if field == "actions":
        return [dict(ACTION) for _ in range(count)]
    return ["123"] * count


@pytest.mark.parametrize("field", LIST_FIELDS)
@pytest.mark.parametrize(
    "builder",
    [
        lambda field, value: AutomodRuleCreateRequest(
            **{k: v for k, v in CREATE_BASE.items() if k != field}, **{field: value}
        ),
        lambda field, value: AutomodRuleUpdateRequest(**{field: value}),
    ],
)
def test_automod_rule_list_ceiling_enforced(builder, field):
    ceiling = MAX_AUTOMOD_ACTIONS if field == "actions" else MAX_AUTOMOD_ID_LIST
    # The ceiling itself is still accepted.
    builder(field, _value_for(field, ceiling))
    # One over is rejected.
    with pytest.raises(ValidationError):
        builder(field, _value_for(field, ceiling + 1))


def test_oversized_list_is_rejected_even_under_body_cap():
    """A huge list is refused at validation, not persisted into a rule row."""
    with pytest.raises(ValidationError):
        AutomodRuleCreateRequest(
            **CREATE_BASE,
            exempt_roles=["123"] * (MAX_AUTOMOD_ID_LIST * 100),
        )
