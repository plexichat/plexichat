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

import re
from typing import Annotated

from pydantic import AfterValidator

from src.core.security.errors import TextRejected
from src.core.security.policies import TEXT_POLICIES, TextPolicy

# Invisible characters that let one string render as another. C0 + DEL + C1
# corrupt terminals and logs; the bidirectional controls reorder displayed
# text so ``evil\u202eexe.exe`` reads as ``evilexe.exe`` without changing the
# stored value. The full Unicode ``Bidi_Control`` set is covered: the Arabic,
# left/right and embedding/override/isolate marks, the Syriac abbreviation mark
# (U+070F, a strong right-to-left control) and the legacy directional formats
# U+206A-U+206F. The zero-width joiner (U+200D) is deliberately absent: it is a
# legitimate part of many emoji sequences and rejecting it would refuse ordinary
# text.
UNSAFE_UNICODE = re.compile(
    r"[\x00-\x1f\x7f-\x9f"  # C0 controls, DEL, C1 controls
    r"\u061c\u200e\u200f\u070f"  # Arabic/LR/RL marks + Syriac abbreviation mark
    r"\u202a-\u202e"  # bidi embedding and overrides
    r"\u2066-\u206f"  # bidi isolates + legacy directional formats (206A-206F)
    r"\u2028\u2029\u2060\ufeff]"  # line/paragraph separators, word joiner, BOM
)


def reject_unsafe_unicode(value: str, field: str = "value") -> str:
    """Reject control characters and bidirectional text overrides.

    This is the shared validator for every policy-annotated text field. It runs
    ahead of the named policy in :func:`policy_checked`, so adding a new text
    policy cannot accidentally opt a field out of this rule.
    """
    if not value:
        return value
    if UNSAFE_UNICODE.search(value):
        raise TextRejected(
            f"{field} must not contain control characters or bidirectional "
            "text overrides",
            field=field,
        )
    return value


def policy_checked(policy_name: str) -> AfterValidator:
    """Build an ``AfterValidator`` that applies a registered text policy.

    The policy name is attached to the validator function so the schema
    coverage test can attribute an annotation to the policy it enforces
    without re-implementing the registry.
    """
    policy: TextPolicy = TEXT_POLICIES[policy_name]

    def check(value: str) -> str:
        text = value or ""
        reject_unsafe_unicode(text, "This field")
        return policy.check(text, "This field")

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
