"""
Version utility module - Zero-friction version parsing and comparison for Python applications.

Version Format: [stage].[major].[minor]-[build]
- stage: a (alpha), b (beta), c (candidate), r (release)
- major: Major version number (1+)
- minor: Minor version number (0+)
- build: Build number, resets on minor version change (1+)

Examples: a.1.0-1, b.2.3-15, r.1.0-1

Usage:
    # In main.py (setup once)
    import utils.version as version
    version.setup(current_version="a.1.0-1")

    # In any other file (no setup needed)
    import utils.version as version
    ver = version.current()
    is_newer = version.compare("r.1.0-1", "a.1.0-1") > 0
    parsed = version.parse("b.2.3-15")
"""

from typing import Optional, Dict, Any
from .core import (
    Version,
    VersionStage,
    parse_version,
    compare_versions,
    is_compatible,
    format_version,
    InvalidVersionError,
    increment_build,
    increment_minor,
    increment_major,
    change_stage,
)

_current_version: Optional[Version] = None
_min_supported_version: Optional[Version] = None
_setup_called = False


def setup(
    current_version: str,
    min_supported_version: Optional[str] = None,
) -> None:
    """
    Setup the version utility. Should be called once in your main application file.

    Args:
        current_version: Current application version string (e.g., "a.1.0-1")
        min_supported_version: Minimum client version supported (optional)
    """
    global _current_version, _min_supported_version, _setup_called

    _current_version = parse_version(current_version)
    if min_supported_version:
        _min_supported_version = parse_version(min_supported_version)
    _setup_called = True


def _ensure_setup() -> None:
    """Internal: Ensures setup was called before using version functions."""
    if not _setup_called:
        raise RuntimeError(
            "Version not configured. Please call version.setup() in your main.py file first."
        )


def current() -> Version:
    """
    Get the current application version.

    Returns:
        Version object representing current version.
    """
    _ensure_setup()
    assert _current_version is not None
    return _current_version


def current_string() -> str:
    """
    Get the current application version as a string.

    Returns:
        Version string (e.g., "a.1.0-1")
    """
    _ensure_setup()
    assert _current_version is not None
    return format_version(_current_version)


def min_supported() -> Optional[Version]:
    """
    Get the minimum supported client version.

    Returns:
        Version object or None if not set.
    """
    _ensure_setup()
    return _min_supported_version


def parse(version_string: str) -> Version:
    """
    Parse a version string into a Version object.

    Args:
        version_string: Version string to parse (e.g., "a.1.0-1")

    Returns:
        Version object.

    Raises:
        InvalidVersionError: If version string is invalid.
    """
    return parse_version(version_string)


def compare(version1: str, version2: str) -> int:
    """
    Compare two version strings.

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        -1 if version1 < version2
         0 if version1 == version2
         1 if version1 > version2
    """
    return compare_versions(version1, version2)


def is_client_compatible(client_version: str) -> bool:
    """
    Check if a client version is compatible with the server.

    Args:
        client_version: Client's version string

    Returns:
        True if compatible, False otherwise.
    """
    _ensure_setup()
    if _min_supported_version is None:
        return True
    return is_compatible(client_version, format_version(_min_supported_version))


def to_dict(version: Version) -> Dict[str, Any]:
    """
    Convert a Version object to a dictionary.

    Args:
        version: Version object

    Returns:
        Dictionary representation of the version.
    """
    return {
        "stage": version.stage.value,
        "major": version.major,
        "minor": version.minor,
        "build": version.build,
        "string": format_version(version),
    }


__all__ = [
    "Version",
    "VersionStage",
    "InvalidVersionError",
    "setup",
    "current",
    "current_string",
    "min_supported",
    "parse",
    "compare",
    "is_client_compatible",
    "to_dict",
    "parse_version",
    "compare_versions",
    "is_compatible",
    "format_version",
    "increment_build",
    "increment_minor",
    "increment_major",
    "change_stage",
]
