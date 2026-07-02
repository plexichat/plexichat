"""
Version parsing and comparison core implementation.

Version Format: [stage].[major].[minor]-[build]
- stage: a (alpha), b (beta), c (candidate), r (release)
- major: Major version number (minimum 1)
- minor: Minor version number (minimum 0)
- build: Build number, resets on minor version change (minimum 1)
"""

import re
from enum import Enum
from typing import Optional
from dataclasses import dataclass


class VersionStage(Enum):
    """Version stage identifiers."""

    ALPHA = "a"
    BETA = "b"
    CANDIDATE = "c"
    RELEASE = "r"


# Stage ordering for comparison (higher = more stable)
STAGE_ORDER = {
    VersionStage.ALPHA: 0,
    VersionStage.BETA: 1,
    VersionStage.CANDIDATE: 2,
    VersionStage.RELEASE: 3,
}


class InvalidVersionError(ValueError):
    """Raised when a version string is invalid."""

    pass


@dataclass(frozen=True)
class Version:
    """Immutable version representation."""

    stage: VersionStage
    major: int
    minor: int
    build: int

    def __post_init__(self):
        if self.major < 1:
            raise InvalidVersionError(f"Major version must be >= 1, got {self.major}")
        if self.minor < 0:
            raise InvalidVersionError(f"Minor version must be >= 0, got {self.minor}")
        if self.build < 1:
            raise InvalidVersionError(f"Build number must be >= 1, got {self.build}")


# Regex pattern for version string: [a|b|c|r].[major].[minor]-[build]
VERSION_PATTERN = re.compile(r"^([abcr])\.(\d+)\.(\d+)-(\d+)$")


def parse_version(version_string: Optional[str]) -> Version:
    """
    Parse a version string into a Version object.

    Args:
        version_string: Version string (e.g., "a.1.0-1")

    Returns:
        Version object

    Raises:
        InvalidVersionError: If version string format is invalid
    """
    if not version_string:
        raise InvalidVersionError("Version string cannot be empty")

    # Check for newlines or null bytes before stripping
    if any(c in version_string for c in "\r\n\x00"):
        raise InvalidVersionError(
            "Version string cannot contain newlines or null bytes"
        )

    version_string = version_string.strip().lower()
    match = VERSION_PATTERN.match(version_string)

    if not match:
        raise InvalidVersionError(
            f"Invalid version format: '{version_string}'. "
            f"Expected format: [a|b|c|r].[major].[minor]-[build] (e.g., 'a.1.0-1')"
        )

    stage_char, major_str, minor_str, build_str = match.groups()

    stage_map = {
        "a": VersionStage.ALPHA,
        "b": VersionStage.BETA,
        "c": VersionStage.CANDIDATE,
        "r": VersionStage.RELEASE,
    }

    stage = stage_map[stage_char]
    major = int(major_str)
    minor = int(minor_str)
    build = int(build_str)

    return Version(stage=stage, major=major, minor=minor, build=build)


def format_version(version: Version) -> str:
    """
    Format a Version object as a string.

    Args:
        version: Version object

    Returns:
        Version string (e.g., "a.1.0-1")
    """
    return f"{version.stage.value}.{version.major}.{version.minor}-{version.build}"


def compare_versions(version1: str, version2: str) -> int:
    """
    Compare two version strings.

    Comparison order: stage -> major -> minor -> build

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        -1 if version1 < version2
         0 if version1 == version2
         1 if version1 > version2
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)

    return compare_version_objects(v1, v2)


def compare_version_objects(v1: Version, v2: Version) -> int:
    """
    Compare two Version objects.

    Args:
        v1: First Version object
        v2: Second Version object

    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
    """
    # Compare stage first
    stage_diff = STAGE_ORDER[v1.stage] - STAGE_ORDER[v2.stage]
    if stage_diff != 0:
        return 1 if stage_diff > 0 else -1

    # Compare major
    if v1.major != v2.major:
        return 1 if v1.major > v2.major else -1

    # Compare minor
    if v1.minor != v2.minor:
        return 1 if v1.minor > v2.minor else -1

    # Compare build
    if v1.build != v2.build:
        return 1 if v1.build > v2.build else -1

    return 0


def is_compatible(client_version: str, min_version: str) -> bool:
    """
    Check if a client version meets the minimum version requirement.

    Args:
        client_version: Client's version string
        min_version: Minimum required version string

    Returns:
        True if client_version >= min_version
    """
    return compare_versions(client_version, min_version) >= 0


def is_same_release_line(version1: str, version2: str) -> bool:
    """
    Check if two versions are on the same release line (same stage and major).

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        True if same stage and major version
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)

    return v1.stage == v2.stage and v1.major == v2.major


def increment_build(version: Version) -> Version:
    """
    Create a new version with incremented build number.

    Args:
        version: Current version

    Returns:
        New Version with build + 1
    """
    return Version(
        stage=version.stage,
        major=version.major,
        minor=version.minor,
        build=version.build + 1,
    )


def increment_minor(version: Version) -> Version:
    """
    Create a new version with incremented minor version (build resets to 1).

    Args:
        version: Current version

    Returns:
        New Version with minor + 1 and build = 1
    """
    return Version(
        stage=version.stage,
        major=version.major,
        minor=version.minor + 1,
        build=1,
    )


def increment_major(version: Version) -> Version:
    """
    Create a new version with incremented major version (minor and build reset).

    Args:
        version: Current version

    Returns:
        New Version with major + 1, minor = 0, build = 1
    """
    return Version(
        stage=version.stage,
        major=version.major + 1,
        minor=0,
        build=1,
    )


def change_stage(version: Version, new_stage: VersionStage) -> Version:
    """
    Create a new version with a different stage (build resets to 1).

    Args:
        version: Current version
        new_stage: New stage to apply

    Returns:
        New Version with updated stage and build = 1
    """
    return Version(
        stage=new_stage,
        major=version.major,
        minor=version.minor,
        build=1,
    )
