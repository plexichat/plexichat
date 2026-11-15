"""Annotated string types that declare which text policy they enforce.

Field annotations are the only place the HTTP boundary states its trust level,
and they are visible to the coverage test (``test_schema_policy_coverage``) in a
way that a per-model ``@field_validator`` is not: the validator's closure cannot
be attributed to a named policy, so nothing could assert that a request model
does not accept free text without one.

Usage::

    class ChannelCreateRequest(BaseModel):
        name: UIText = Field(..., min_length=1, max_length=100)

``UIText`` rejects HTML metacharacters and control characters through the shared
``ui_text`` policy, exactly as the per-route helper it replaces did.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator

from src.core.security.policies import TEXT_POLICIES, TextPolicy


def policy_checked(policy_name: str) -> AfterValidator:
    """Build an ``AfterValidator`` that applies a registered text policy.

    The policy name is attached to the validator function so the schema
    coverage test can attribute an annotation to the policy it enforces
    without re-implementing the registry.
    """
    policy: TextPolicy = TEXT_POLICIES[policy_name]

    def check(value: str) -> str:
        return policy.check(value or "", "This field")

    setattr(check, "__policy_name__", policy_name)
    setattr(check, "__policy__", policy)
    return AfterValidator(check)


#: Short, user-facing text echoed to other members (names, topics, tags).
UIText = Annotated[str, policy_checked("ui_text")]
#: ``UIText`` with a 200 character ceiling.
UIText200 = Annotated[str, policy_checked("ui_text_200")]
#: ``UIText`` with a 500 character ceiling (descriptions, bios).
UIText500 = Annotated[str, policy_checked("ui_text_500")]

POLICY_ANNOTATIONS: dict[str, object] = {
    "ui_text": UIText,
    "ui_text_200": UIText200,
    "ui_text_500": UIText500,
}
