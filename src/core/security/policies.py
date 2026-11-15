"""Text policies: the only sanctioned way to accept user-facing text.

Before this module every route that renders user text back to other members
carried its own copy of "reject these characters" -- emoji names, sticker tags,
sound names, channel topics, server descriptions, usernames. Each copy was
correct and each was a place the next feature would forget.

A policy is an object rather than a helper function so that:

* a schema field can *name* the trust level it enforces (see
  ``src/api/schemas/policy_types.py``), and
* the coverage test can enumerate the registry and assert that a request model
  does not accept free text without one.

Rejection here is defence in depth, never the primary defence: the client still
escapes on render. These policies exist so a stored value cannot become
dangerous if an encoder is bypassed or a new client renders the column raw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from .errors import TextRejected, TextTooLong

# Characters that terminate or escape an HTML attribute or text node. The '='
# is included because it turns an unquoted attribute value into a second
# attribute (``<a href=x onmouseover=alert(1)>``).
HTML_META_CHARS: Final[frozenset[str]] = frozenset("<>\"'&=")


def is_control_character(character: str) -> bool:
    """True for C0 controls and DEL, which corrupt terminals and logs."""
    return ord(character) < 0x20 or ord(character) == 0x7F


class TextPolicy(Protocol):
    """A named, reusable rule over user text.

    ``name`` is a read-only property so the frozen dataclass implementations
    satisfy it; a writable attribute in the protocol would require every policy
    to be mutable.
    """

    @property
    def name(self) -> str: ...

    def check(self, value: str, field: str = "value") -> str:
        """Return ``value`` unchanged, or raise a ``TextRejected`` subclass."""
        ...


@dataclass(frozen=True)
class RejectUICharacters:
    """Reject HTML metacharacters and control characters.

    Used for short text that is echoed to other members: names, topics,
    descriptions, tags. The message is deliberately identical to the one the
    per-route helpers raised, so refactoring a call site cannot change what a
    client sees.
    """

    name: str = "ui_text"

    def check(self, value: str, field: str = "value") -> str:
        if not value:
            return value
        offending = any(
            character in HTML_META_CHARS or is_control_character(character)
            for character in value
        )
        if offending:
            raise TextRejected(
                f"{field} must not contain HTML metacharacters or control characters",
                field=field,
            )
        return value


@dataclass(frozen=True)
class MaxLength:
    """Reject text longer than ``limit`` characters.

    Separate from Pydantic's ``max_length`` because core modules (username
    validation, profile bio) enforce limits without a model in hand.
    """

    limit: int
    name: str = "max_length"

    def check(self, value: str, field: str = "value") -> str:
        if value is not None and len(value) > self.limit:
            raise TextTooLong(
                f"{field} must be at most {self.limit} characters",
                field=field,
            )
        return value


@dataclass(frozen=True)
class Composite:
    """Apply several policies in order, first failure wins."""

    policies: tuple[TextPolicy, ...]
    name: str = "composite"

    def check(self, value: str, field: str = "value") -> str:
        for policy in self.policies:
            value = policy.check(value, field)
        return value


# The registry the schema coverage test enumerates. Adding a policy here makes
# it available as an annotated field type; removing one must break that test.
TEXT_POLICIES: Final[dict[str, TextPolicy]] = {
    "ui_text": RejectUICharacters(),
    "ui_text_200": Composite(
        (RejectUICharacters(), MaxLength(200)), name="ui_text_200"
    ),
    "ui_text_500": Composite(
        (RejectUICharacters(), MaxLength(500)), name="ui_text_500"
    ),
}


def text_policy(name: str) -> TextPolicy:
    """Look up a registered policy by name."""
    try:
        return TEXT_POLICIES[name]
    except KeyError as exc:  # pragma: no cover - programming error
        raise ValueError(f"Unknown text policy {name!r}") from exc


def check_text(name: str, value: str, field: str = "value") -> str:
    """Apply a registered policy to ``value``."""
    return text_policy(name).check(value, field)
