"""Snowflake-id serialization for gateway payloads.

A JavaScript ``Number`` cannot represent a 64-bit snowflake exactly, so an id
served as a JSON number is silently rounded by the client, which then derives
mismatched keys or addresses the wrong entity. Every gateway payload must
therefore carry ids as decimal strings.

This module is the single source of truth for that rule. Emitters call
:func:`stringify_snowflake_ids` on the dicts they hand out, and the websocket
dispatcher applies it again at the wire boundary as a backstop so a
hand-built ``Event`` cannot leak a numeric id.
"""

from typing import Any, Dict

# Explicit id keys that do not match the ``*_id``/``*_ids`` suffix rule.
_ID_FIELDS = frozenset({"id"})


def is_id_field(key: str) -> bool:
    """Return True when a payload key carries a snowflake id value."""
    return key in _ID_FIELDS or key.endswith("_id") or key.endswith("_ids")


def stringify_snowflake_ids(value: Any) -> Any:
    """Recursively convert snowflake-id fields to decimal strings.

    ``id``, ``*_id`` and ``*_ids`` keys are stringified; a ``*_ids`` list has
    each element stringified individually. Non-id keys and their values are
    preserved (numbers such as timestamps, flags and counts stay numeric).

    Dicts and lists are rebuilt, so the caller's object is never mutated.
    """
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if is_id_field(key):
                if isinstance(item, (list, tuple)):
                    out[key] = [None if v is None else str(v) for v in item]
                else:
                    out[key] = None if item is None else str(item)
            else:
                out[key] = stringify_snowflake_ids(item)
        return out
    if isinstance(value, list):
        return [stringify_snowflake_ids(item) for item in value]
    return value
