"""Typed, fail-closed access to the media security configuration.

``FileValidator`` used to read ``config.get("media", {}).get("security", {})``
into a plain dict and then trust every key. A misspelled key silently meant
"absent", and an absent key fell back to the *permissive* default -- so a typo
in an operator's config could disable magic-byte validation with nothing in the
logs to say so.

This object makes the defaults explicit and refuses a value of the wrong type
instead of coercing it. It is a value object: build it once, pass it around,
and there is no dict-key archaeology at the point of use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable, Mapping


class ConfigTypeError(ValueError):
    """A configured security value has the wrong type or shape."""


def _require_bool(mapping: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in mapping:
        return default
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigTypeError(
            f"media.security.{key} must be a boolean, got {type(value).__name__}"
        )
    return value


def _require_str_set(
    mapping: Mapping[str, Any],
    key: str,
    default: Iterable[str],
) -> FrozenSet[str]:
    if key not in mapping:
        return frozenset(default)
    value = mapping[key]
    # A bare string is iterable, and every character would silently become an
    # "extension"; require an explicit collection.
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ConfigTypeError(
            f"media.security.{key} must be a list of strings, got "
            f"{type(value).__name__}"
        )
    items = set()
    for item in value:
        if not isinstance(item, str):
            raise ConfigTypeError(
                f"media.security.{key} must contain only strings, got "
                f"{type(item).__name__}"
            )
        items.add(item)
    return frozenset(items)


@dataclass(frozen=True)
class FileSecuritySettings:
    """The security knobs ``FileValidator`` acts on."""

    magic_byte_validation: bool = True
    block_executables: bool = True
    blocked_extensions: FrozenSet[str] = frozenset()
    blocked_mime_types: FrozenSet[str] = frozenset()

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Any] | None,
        *,
        default_extensions: Iterable[str],
        default_mime_types: Iterable[str],
    ) -> "FileSecuritySettings":
        security = mapping or {}
        return cls(
            magic_byte_validation=_require_bool(
                security, "magic_byte_validation", True
            ),
            block_executables=_require_bool(security, "block_executables", True),
            blocked_extensions=_require_str_set(
                security, "blocked_extensions", default_extensions
            ),
            blocked_mime_types=_require_str_set(
                security, "blocked_mime_types", default_mime_types
            ),
        )
