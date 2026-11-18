"""Symlink-safe path handling for artifact storage.

A stored artifact path is attacker-influenced data (it often originates in an
upload filename or a DSAR export key). Joining it onto a storage base with a
plain ``os.path.join`` is not enough: a symlink anywhere in the chain can make
the resolved path point outside the base directory, and a ``..`` segment or an
absolute component can escape without any symlink at all.

Every function here returns a fully resolved path that is *provably* contained
within the caller supplied base, or raises :class:`UnsafeStoragePath`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class UnsafeStoragePath(ValueError):
    """Raised when a path cannot be confined to its storage base."""


def _reject_unsafe_part(part: object, *, label: str = "path") -> str:
    if not isinstance(part, str):
        raise UnsafeStoragePath(f"{label} must be a string")
    if not part:
        raise UnsafeStoragePath(f"empty {label}")
    if "\x00" in part or _CONTROL_RE.search(part):
        raise UnsafeStoragePath(f"control character in {label}")
    # Backslashes are separators on Windows clients even when the server runs
    # on POSIX; treating them as ordinary characters would hide traversal.
    if "\\" in part:
        raise UnsafeStoragePath(f"backslash in {label}")
    if part.startswith("/") or part.startswith("//") or _DRIVE_RE.match(part):
        raise UnsafeStoragePath(f"absolute {label}")
    segments = part.split("/")
    if any(segment == ".." for segment in segments):
        raise UnsafeStoragePath(f"traversal in {label}")
    return part


def safe_storage_path(
    base: str | os.PathLike[str],
    *parts: str,
    must_exist: bool = False,
) -> Path:
    """Resolve ``parts`` under ``base``, defeating traversal and symlinks.

    ``base`` must be an existing directory. Each part may contain ``/``
    separated subdirectories but must be relative and free of ``..``. The
    result is fully resolved (all symlinks followed) and checked against the
    resolved base before it is returned, so callers can safely open it.
    """
    base_path = Path(base)
    if not base_path.is_absolute():
        base_path = base_path.absolute()
    try:
        base_resolved = base_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise UnsafeStoragePath("storage base is not accessible") from exc
    if not base_resolved.is_dir():
        raise UnsafeStoragePath("storage base is not a directory")

    candidate = base_resolved
    for part in parts:
        _reject_unsafe_part(part, label="storage path")
        candidate = candidate.joinpath(*part.split("/"))

    # Resolve *after* joining so intermediate symlinks are followed; a symlink
    # that points at /etc (or at a sibling tenant's directory) is caught by the
    # containment check below rather than being silently trusted.
    try:
        resolved = candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise UnsafeStoragePath("storage path is not accessible") from exc

    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise UnsafeStoragePath("storage path escapes its base directory")

    if must_exist and not (resolved.is_file() or resolved.is_dir()):
        raise UnsafeStoragePath("storage path does not exist")
    return resolved


def safe_storage_key(key: object) -> str:
    """Normalise a *relative* storage key without touching the filesystem.

    Use this for keys persisted in a database before passing them to
    :func:`safe_storage_path`. Unlike that function this is a purely lexical
    check, so it can validate keys for objects that are not stored locally.
    """
    if not isinstance(key, str):
        raise UnsafeStoragePath("storage key must be a string")
    text = key.strip()
    if not text or "\x00" in text or _CONTROL_RE.search(text):
        raise UnsafeStoragePath("invalid storage key")
    text = text.replace("\\", "/")
    if text.startswith("/") or _DRIVE_RE.match(text):
        raise UnsafeStoragePath("absolute storage key")

    segments: list[str] = []
    for segment in text.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise UnsafeStoragePath("traversal in storage key")
        segments.append(segment)
    if not segments:
        raise UnsafeStoragePath("empty storage key")
    return "/".join(segments)
