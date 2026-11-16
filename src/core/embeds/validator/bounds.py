"""Hard resource bounds for untrusted embed payloads.

Embed dicts accepted by the message API are stored verbatim and rendered by
clients, so the sanitizer recurses into arbitrary dict/list shapes. The
per-field length limits in :mod:`constants` stop one oversized string, but a
small payload can still be a wide or deep tree that exhausts the interpreter
stack or the process heap before those limits ever apply.

This module is the single place that defines the *structural* budget for that
tree. It is deliberately independent of the sanitizers so both the message
sanitizer (:mod:`message`) and the embed-data validator (:mod:`schema`) can
charge the same budget. The walker is iterative (an explicit stack, never
Python recursion) so checking a hostile tree cannot itself blow the stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Tuple

from .constants import (
    AUTHOR_NAME_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    FIELD_NAME_MAX_LENGTH,
    FIELD_VALUE_MAX_LENGTH,
    FOOTER_TEXT_MAX_LENGTH,
    TITLE_MAX_LENGTH,
)
from ..exceptions import EmbedValidationError

# Nesting depth is also enforced by the recursive message sanitizer; keeping
# the value here makes the structural budget and the recursion guard share one
# definition.
MAX_EMBED_NESTING_DEPTH = 8

# Total container/scalar nodes allowed across one embed tree. A legitimate
# rich embed (title, description, a handful of fields, one image) is well
# under 200 nodes; 1024 leaves generous headroom while capping allocation.
MAX_EMBED_TOTAL_NODES = 1024

# Per-container fan-out. Discord-style embeds have at most 25 fields, so 64
# entries per list and 32 keys per object cannot reject a legitimate payload.
MAX_EMBED_LIST_ITEMS = 64
MAX_EMBED_KEYS_PER_OBJECT = 32

# Total characters across every string in one embed tree. Independent of the
# per-field caps and of TOTAL_CHAR_LIMIT (which only counts a fixed set of
# top-level fields); this catches many small nested strings adding up.
MAX_EMBED_TOTAL_TEXT_CHARS = 16384

# Hard cap applied to every string encountered by the recursive sanitizer,
# including string values under unknown/custom keys. Known keys use their
# stricter per-field limit from the map below.
GENERIC_STRING_MAX_LENGTH = 4096

TEXT_FIELD_LIMITS = {
    "title": TITLE_MAX_LENGTH,
    "description": DESCRIPTION_MAX_LENGTH,
    "name": max(FIELD_NAME_MAX_LENGTH, AUTHOR_NAME_MAX_LENGTH),
    "value": FIELD_VALUE_MAX_LENGTH,
    "text": FOOTER_TEXT_MAX_LENGTH,
}


@dataclass
class EmbedBudget:
    """Mutable per-payload accounting handed through the sanitizers."""

    nodes: int = 0
    text_chars: int = 0

    def charge_node(self, path: str) -> None:
        self.nodes += 1
        if self.nodes > MAX_EMBED_TOTAL_NODES:
            raise EmbedValidationError(
                f"{path} exceeds the maximum embed node budget "
                f"({MAX_EMBED_TOTAL_NODES})",
                [path],
            )

    def charge_text(self, value: Any, path: str, max_length: int | None = None) -> str:
        """Account for a string value and enforce its length, returning it."""
        text = value if isinstance(value, str) else str(value)
        self.text_chars += len(text)
        if self.text_chars > MAX_EMBED_TOTAL_TEXT_CHARS:
            raise EmbedValidationError(
                f"{path} exceeds the maximum embed text budget "
                f"({MAX_EMBED_TOTAL_TEXT_CHARS} characters)",
                [path],
            )
        limit = max_length if max_length is not None else GENERIC_STRING_MAX_LENGTH
        if len(text) > limit:
            raise EmbedValidationError(
                f"{path} exceeds maximum length of {limit} characters",
                [f"{path}: {len(text)}/{limit}"],
            )
        return text


def field_string_limit(key: str) -> int:
    """Return the hard string cap for a key used by the recursive sanitizer."""
    return TEXT_FIELD_LIMITS.get(key, GENERIC_STRING_MAX_LENGTH)


def _walk(root: Any, field_name: str, enforce_field_lengths: bool = True) -> None:
    """Iteratively charge every node in an embed tree against a fresh budget."""
    budget = EmbedBudget()
    # (value, path, depth) tuples on an explicit stack: no Python recursion.
    stack: list[Tuple[Any, str, int]] = [(root, field_name, 0)]
    while stack:
        value, path, depth = stack.pop()
        if depth > MAX_EMBED_NESTING_DEPTH:
            raise EmbedValidationError(
                f"{path} exceeds maximum embed nesting depth "
                f"({MAX_EMBED_NESTING_DEPTH})",
                [path],
            )
        budget.charge_node(path)

        if isinstance(value, dict):
            if len(value) > MAX_EMBED_KEYS_PER_OBJECT:
                raise EmbedValidationError(
                    f"{path} has too many keys ({len(value)}/"
                    f"{MAX_EMBED_KEYS_PER_OBJECT})",
                    [path],
                )
            children: Iterable[Tuple[Any, str, int]] = (
                (child, f"{path}.{key}", depth + 1) for key, child in value.items()
            )
            stack.extend(children)
        elif isinstance(value, (list, tuple)):
            if len(value) > MAX_EMBED_LIST_ITEMS:
                raise EmbedValidationError(
                    f"{path} has too many items ({len(value)}/{MAX_EMBED_LIST_ITEMS})",
                    [path],
                )
            stack.extend(
                (child, f"{path}[{i}]", depth + 1) for i, child in enumerate(value)
            )
        elif isinstance(value, str):
            if enforce_field_lengths:
                # The leaf key is the last path component after the final '.'.
                leaf = path.rsplit(".", 1)[-1]
                budget.charge_text(value, path, field_string_limit(leaf))
            else:
                # The embed-data validator enforces per-field lengths itself
                # (as collected *issues*, not raised errors); here we only
                # charge the aggregate text budget.
                budget.charge_text(value, path)


def enforce_embed_budget(
    embed_data: Any, field_name: str = "embed", *, enforce_field_lengths: bool = True
) -> None:
    """Reject an embed-shaped value that would exhaust the parsing budget.

    Args:
        embed_data: The untrusted embed-shaped value to check.
        field_name: Label used in error paths.
        enforce_field_lengths: When true, also cap each string at its
            per-field limit. Callers that report per-field lengths as
            validation issues (rather than raising) pass ``False``.

    Raises:
        EmbedValidationError: If the tree is too deep, too wide or too large.
    """
    _walk(embed_data, field_name, enforce_field_lengths=enforce_field_lengths)
